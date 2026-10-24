from __future__ import annotations

import base64
import hashlib
import importlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from typing import Any

import pytest
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.application import FullMessageSnapshot, GetMailboxProfile, IngestLabels
from gmail_ingestion.auth.provider import GmailOAuthProvider
from gmail_ingestion.auth.store import LocalCredentialStore
from gmail_ingestion.config import GMAIL_READONLY_SCOPE, OAuthSettings
from gmail_ingestion.domain import GmailAccount


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


class _RefreshState:
    def __init__(self) -> None:
        self.refreshes = 0


class _Credentials:
    def __init__(self, token: str, state: _RefreshState) -> None:
        self.token = token
        self.state = state
        self.valid = token == "new-access-token"
        self.expired = not self.valid
        self.refresh_token = "refresh-token"

    def refresh(self, request: Any) -> None:
        self.state.refreshes += 1
        self.token = "new-access-token"
        self.valid = True
        self.expired = False

    def to_json(self) -> str:
        return json.dumps(
            {
                "token": self.token,
                "refresh_token": self.refresh_token,
                "client_id": "client-id",
                "client_secret": "client-secret",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": [GMAIL_READONLY_SCOPE],
            }
        )


def _provider(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    message_count: int,
    thread_count: int,
    history_id: str,
) -> tuple[GmailOAuthProvider, str, _RefreshState]:
    store = LocalCredentialStore(root)
    account_id = store.find_or_create_account_id("qualification@example.com")
    store.save_account(
        GmailAccount(
            account_id,
            "qualification@example.com",
            message_count,
            thread_count,
            history_id,
        )
    )
    store.save_credentials_json(
        account_id,
        json.dumps(
            {
                "token": "expired-access-token",
                "refresh_token": "refresh-token",
                "client_id": "client-id",
                "client_secret": "client-secret",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": [GMAIL_READONLY_SCOPE],
            }
        ),
    )
    store.set_active_account(account_id)
    state = _RefreshState()
    original_import = importlib.import_module

    class _CredentialsClass:
        @staticmethod
        def from_authorized_user_info(
            payload: dict[str, Any],
            scopes: list[str],
        ) -> _Credentials:
            assert scopes == [GMAIL_READONLY_SCOPE]
            token = payload.get("token")
            assert isinstance(token, str)
            return _Credentials(token, state)

    def fake_import(name: str) -> Any:
        if name == "google.oauth2.credentials":
            return SimpleNamespace(Credentials=_CredentialsClass)
        if name == "google.auth.transport.requests":
            return SimpleNamespace(Request=lambda: object())
        return original_import(name)

    monkeypatch.setattr("gmail_ingestion.auth.provider.importlib.import_module", fake_import)
    settings = OAuthSettings(credential_dir=store.root)
    return GmailOAuthProvider(settings, store), account_id, state


class _QualificationBackend:
    def __init__(
        self,
        messages: Sequence[Mapping[str, Any]],
        *,
        page_size: int = 500,
        transient_message_failures: Mapping[str, int] | None = None,
        transient_attachment_failures: Mapping[str, int] | None = None,
    ) -> None:
        self.message_payloads = {
            str(message["id"]): dict(message)
            for message in messages
        }
        self.message_ids = list(self.message_payloads)
        self.page_size = page_size
        self.transient_message_failures = dict(transient_message_failures or {})
        self.transient_attachment_failures = dict(transient_attachment_failures or {})
        self.message_attempts: dict[str, int] = {}
        self.attachment_attempts: dict[str, int] = {}
        self.list_calls = 0
        self.lock = Lock()

        self.labels = [
            {"id": "INBOX", "name": "INBOX", "type": "system"},
            {"id": "Label_1", "name": "Qualification", "type": "user"},
        ]
        self.label_details = {
            "INBOX": {
                "id": "INBOX",
                "name": "INBOX",
                "type": "system",
                "messagesTotal": len(messages),
                "messagesUnread": 0,
                "threadsTotal": max(1, len(messages)),
                "threadsUnread": 0,
            },
            "Label_1": {
                "id": "Label_1",
                "name": "Qualification",
                "type": "user",
                "messageListVisibility": "show",
                "labelListVisibility": "labelShow",
                "messagesTotal": len(messages),
                "messagesUnread": 0,
                "threadsTotal": max(1, len(messages)),
                "threadsUnread": 0,
            },
        }

    def profile(self) -> Mapping[str, Any]:
        threads = {
            str(message.get("threadId", message["id"]))
            for message in self.message_payloads.values()
        }
        return {
            "emailAddress": "qualification@example.com",
            "messagesTotal": len(self.message_payloads),
            "threadsTotal": len(threads),
            "historyId": "9000",
        }

    def list_page(self, page_token: str | None) -> Mapping[str, Any]:
        with self.lock:
            self.list_calls += 1
        start = int(page_token or "0")
        end = min(start + self.page_size, len(self.message_ids))
        refs = [
            {
                "id": message_id,
                "threadId": self.message_payloads[message_id].get("threadId", message_id),
            }
            for message_id in self.message_ids[start:end]
        ]
        payload: dict[str, Any] = {
            "messages": refs,
            "resultSizeEstimate": len(self.message_ids),
        }
        if end < len(self.message_ids):
            payload["nextPageToken"] = str(end)
        return payload

    def get_message(self, message_id: str) -> Mapping[str, Any]:
        with self.lock:
            attempts = self.message_attempts.get(message_id, 0) + 1
            self.message_attempts[message_id] = attempts
        if attempts <= self.transient_message_failures.get(message_id, 0):
            from gmail_ingestion.api import GmailApiError

            raise GmailApiError(f"transient message failure for {message_id}")
        return self.message_payloads[message_id]

    def get_attachment(self, attachment_id: str) -> Mapping[str, Any]:
        with self.lock:
            attempts = self.attachment_attempts.get(attachment_id, 0) + 1
            self.attachment_attempts[attachment_id] = attempts
        if attempts <= self.transient_attachment_failures.get(attachment_id, 0):
            from gmail_ingestion.api import GmailApiError

            raise GmailApiError(f"transient attachment failure for {attachment_id}")
        assert attachment_id == "att-1"
        return {"attachmentId": attachment_id, "size": 7, "data": _b64(b"PDFDATA")}


class _LabelsApi:
    def __init__(self, backend: _QualificationBackend) -> None:
        self.backend = backend

    def list_labels(self) -> Mapping[str, Any]:
        return {"labels": self.backend.labels}

    def get_label(self, label_id: str) -> Mapping[str, Any]:
        return self.backend.label_details[label_id]


class _MessagesApi:
    def __init__(self, backend: _QualificationBackend) -> None:
        self.backend = backend

    def list_messages(
        self,
        *,
        page_token: str | None = None,
        max_results: int = 500,
        include_spam_trash: bool = False,
        query: str | None = None,
        label_ids: Sequence[str] = (),
    ) -> Mapping[str, Any]:
        assert 1 <= max_results <= 500
        assert include_spam_trash is False
        assert query is None
        assert label_ids == ()
        return self.backend.list_page(page_token)

    def get_message(self, message_id: str, *, format: str = "full") -> Mapping[str, Any]:
        assert format == "full"
        return self.backend.get_message(message_id)


class _AttachmentsApi:
    def __init__(self, backend: _QualificationBackend) -> None:
        self.backend = backend

    def get_attachment(self, message_id: str, attachment_id: str) -> Mapping[str, Any]:
        assert message_id == "m-attachment"
        return self.backend.get_attachment(attachment_id)


class _Client:
    def __init__(self, backend: _QualificationBackend) -> None:
        self.backend = backend
        self.labels = _LabelsApi(backend)
        self.messages = _MessagesApi(backend)
        self.attachments = _AttachmentsApi(backend)

    def get_profile(self) -> Mapping[str, Any]:
        return self.backend.profile()


def _factory(backend: _QualificationBackend):
    def factory(**kwargs: object) -> _Client:
        assert "credentials" in kwargs
        return _Client(backend)

    return factory


def _small_mailbox_messages() -> list[Mapping[str, Any]]:
    return [
        {
            "id": "m-text",
            "threadId": "thread-shared",
            "historyId": "9001",
            "payload": {
                "partId": "",
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "Alice <alice@example.com>"},
                    {"name": "To", "value": "Qualification <qualification@example.com>"},
                    {"name": "Subject", "value": "Plain text"},
                    {"name": "Content-Type", "value": "text/plain; charset=utf-8"},
                ],
                "body": {"size": 5, "data": _b64(b"hello")},
            },
        },
        {
            "id": "m-html",
            "threadId": "thread-shared",
            "historyId": "9002",
            "payload": {
                "partId": "",
                "mimeType": "text/html",
                "headers": [
                    {"name": "Subject", "value": "HTML"},
                    {"name": "Content-Type", "value": "text/html; charset=utf-8"},
                ],
                "body": {"size": 11, "data": _b64(b"<p>hello</p>")},
            },
        },
        {
            "id": "m-multipart",
            "threadId": "thread-multipart",
            "historyId": "9003",
            "payload": {
                "partId": "",
                "mimeType": "multipart/alternative",
                "headers": [{"name": "Subject", "value": "Multipart"}],
                "body": {"size": 0},
                "parts": [
                    {
                        "partId": "0",
                        "mimeType": "text/plain",
                        "headers": [{"name": "Content-Type", "value": "text/plain; charset=utf-8"}],
                        "body": {"size": 5, "data": _b64(b"plain")},
                    },
                    {
                        "partId": "1",
                        "mimeType": "text/html",
                        "headers": [{"name": "Content-Type", "value": "text/html; charset=utf-8"}],
                        "body": {"size": 11, "data": _b64(b"<b>html</b>")},
                    },
                ],
            },
        },
        {
            "id": "m-attachment",
            "threadId": "thread-attachment",
            "historyId": "9004",
            "payload": {
                "partId": "",
                "mimeType": "multipart/mixed",
                "headers": [{"name": "Subject", "value": "Attachment"}],
                "body": {"size": 0},
                "parts": [
                    {
                        "partId": "0",
                        "mimeType": "text/plain",
                        "headers": [{"name": "Content-Type", "value": "text/plain; charset=utf-8"}],
                        "body": {"size": 4, "data": _b64(b"body")},
                    },
                    {
                        "partId": "1",
                        "mimeType": "application/pdf",
                        "filename": "proof.pdf",
                        "headers": [
                            {
                                "name": "Content-Disposition",
                                "value": "attachment; filename=proof.pdf",
                            }
                        ],
                        "body": {"size": 7, "attachmentId": "att-1"},
                    },
                ],
            },
        },
        {
            "id": "m-retry",
            "threadId": "thread-retry",
            "historyId": "9005",
            "payload": {
                "partId": "",
                "mimeType": "text/plain",
                "headers": [
                    {"name": "Content-Type", "value": "text/plain; charset=iso-8859-1"}
                ],
                "body": {"size": 4, "data": _b64("café".encode("iso-8859-1"))},
            },
        },
    ]


def test_rc1_foundation_flow_covers_refresh_labels_mime_threads_and_attachment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = _small_mailbox_messages()
    provider, account_id, refresh_state = _provider(
        tmp_path / "credentials",
        monkeypatch,
        message_count=len(messages),
        thread_count=4,
        history_id="9000",
    )
    backend = _QualificationBackend(
        messages,
        transient_message_failures={"m-retry": 1},
        transient_attachment_failures={"att-1": 1},
    )
    factory = _factory(backend)
    store = LocalArtifactStore(tmp_path / "artifacts")

    profile = GetMailboxProfile(provider, client_factory=factory).run()
    labels = IngestLabels(provider, store, client_factory=factory).run()
    snapshot = FullMessageSnapshot(
        provider,
        store,
        client_factory=factory,
        retry_sleep=lambda _: None,
    ).run(
        download_attachments=True,
        max_attempts=3,
        max_workers=3,
    )

    assert profile.account_id == account_id
    assert profile.messages_total == 5
    assert refresh_state.refreshes == 1
    _, persisted_credentials = provider.store.load_credentials_info(account_id)
    assert persisted_credentials["token"] == "new-access-token"

    assert labels.label_count == 2
    assert labels.raw_artifact_count == 3
    assert labels.parquet_path.is_file()
    label_rows = labels.dataset.to_rows()
    assert {row["label_type"] for row in label_rows} == {"system", "user"}

    assert snapshot.manifest_status == "COMPLETED"
    assert snapshot.discovered_count == 5
    assert snapshot.fetched_count == 5
    assert snapshot.failed_count == 0
    assert snapshot.mime_messages_processed == 5
    assert snapshot.attachments_detected == 1
    assert snapshot.attachments_fetched == 1
    assert snapshot.bytes_downloaded == 7
    assert backend.message_attempts["m-retry"] == 2
    assert backend.attachment_attempts["att-1"] == 2

    message_root = store.run_root("gmail.messages", snapshot.run_id)
    raw_text = json.loads(
        (message_root / "raw" / "message-m-text-full.json").read_text(encoding="utf-8")
    )
    raw_html = json.loads(
        (message_root / "raw" / "message-m-html-full.json").read_text(encoding="utf-8")
    )
    assert raw_text["threadId"] == raw_html["threadId"] == "thread-shared"

    text_analysis = json.loads(
        (message_root / "candidate" / "mime" / "m-text.json").read_text(encoding="utf-8")
    )
    html_analysis = json.loads(
        (message_root / "candidate" / "mime" / "m-html.json").read_text(encoding="utf-8")
    )
    multipart_analysis = json.loads(
        (message_root / "candidate" / "mime" / "m-multipart.json").read_text(encoding="utf-8")
    )
    retry_analysis = json.loads(
        (message_root / "candidate" / "mime" / "m-retry.json").read_text(encoding="utf-8")
    )
    attachment_analysis = json.loads(
        (message_root / "candidate" / "mime" / "m-attachment.json").read_text(encoding="utf-8")
    )

    assert text_analysis["text_body"] == "hello"
    assert html_analysis["html_body"] == "<p>hello</p>"
    assert multipart_analysis["text_body"] == "plain"
    assert multipart_analysis["html_body"] == "<b>html</b>"
    assert retry_analysis["text_body"] == "café"

    attachment = attachment_analysis["attachments"][0]
    assert attachment["download_status"] == "DOWNLOADED"
    assert attachment["content_hash_sha256"] == hashlib.sha256(b"PDFDATA").hexdigest()

    binary_root = store.run_root("gmail.attachments", snapshot.run_id) / "raw"
    binaries = list(binary_root.iterdir())
    assert len(binaries) == 1
    assert binaries[0].read_bytes() == b"PDFDATA"

    manifest = json.loads(Path(snapshot.manifest_path).read_text(encoding="utf-8"))
    assert manifest["status"] == "COMPLETED"
    assert manifest["connector_version"] == "0.1.0rc1"
    assert manifest["messages_discovered"] == 5
    assert manifest["messages_fetched"] == 5
    assert manifest["attachments_fetched"] == 1
    assert manifest["errors"] == []

    artifact_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (tmp_path / "artifacts").rglob("*")
        if path.is_file() and path.suffix in {".json", ".txt"}
    )
    assert "refresh-token" not in artifact_text
    assert "new-access-token" not in artifact_text
    assert "client-secret" not in artifact_text


def test_rc1_empty_mailbox_produces_complete_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, _, _ = _provider(
        tmp_path / "credentials",
        monkeypatch,
        message_count=0,
        thread_count=0,
        history_id="9100",
    )
    backend = _QualificationBackend([])
    result = FullMessageSnapshot(
        provider,
        LocalArtifactStore(tmp_path / "artifacts"),
        client_factory=_factory(backend),
        retry_sleep=lambda _: None,
    ).run()

    assert result.manifest_status == "COMPLETED"
    assert result.pages_scanned == 1
    assert result.discovered_count == 0
    assert result.fetched_count == 0
    assert result.mime_messages_processed == 0


def test_rc1_large_mailbox_sample_crosses_three_pages_without_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    messages = [
        {
            "id": f"m-{index:04d}",
            "threadId": f"t-{index // 2:04d}",
            "historyId": str(10000 + index),
            "payload": {
                "partId": "",
                "mimeType": "text/plain",
                "headers": [{"name": "Content-Type", "value": "text/plain; charset=utf-8"}],
                "body": {"size": 0},
            },
        }
        for index in range(1001)
    ]
    provider, _, _ = _provider(
        tmp_path / "credentials",
        monkeypatch,
        message_count=len(messages),
        thread_count=501,
        history_id="10000",
    )
    backend = _QualificationBackend(messages, page_size=500)
    store = LocalArtifactStore(tmp_path / "artifacts")
    result = FullMessageSnapshot(
        provider,
        store,
        client_factory=_factory(backend),
        retry_sleep=lambda _: None,
    ).run(max_results=500, max_workers=8)

    assert result.manifest_status == "COMPLETED"
    assert result.pages_scanned == 3
    assert backend.list_calls == 3
    assert result.discovered_count == 1001
    assert result.fetched_count == 1001
    assert result.failed_count == 0
    assert result.raw_message_count == 1001
    assert result.mime_messages_processed == 1001

    raw_files = list((store.run_root("gmail.messages", result.run_id) / "raw").glob("*.json"))
    mime_files = list(
        (store.run_root("gmail.messages", result.run_id) / "candidate" / "mime").glob("*.json")
    )
    assert len(raw_files) == 1001
    assert len(mime_files) == 1001

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["messages_discovered"] == 1001
    assert manifest["messages_fetched"] == 1001
    assert manifest["messages_failed"] == 0
