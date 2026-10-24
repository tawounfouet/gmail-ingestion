from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.api import GmailApiError
from gmail_ingestion.application import FullMessageSnapshot
from gmail_ingestion.auth.store import LocalCredentialStore
from gmail_ingestion.domain import GmailAccount


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


class _Provider:
    def __init__(self, root: Path) -> None:
        self.store = LocalCredentialStore(root)
        self.credentials = object()
        self.account_id = self.store.find_or_create_account_id("user@example.com")
        self.store.save_account(
            GmailAccount(self.account_id, "user@example.com", 1, 1, "500")
        )
        self.store.set_active_account(self.account_id)

    def load(self, account_id: str | None = None) -> tuple[str, object]:
        return self.store.resolve_account_id(account_id), self.credentials


class _Backend:
    def __init__(self, *, fail_attachment: bool = False) -> None:
        self.message_get_calls = 0
        self.attachment_calls = 0
        self.fail_attachment = fail_attachment

    def message(self) -> Mapping[str, Any]:
        self.message_get_calls += 1
        return {
            "id": "m1",
            "threadId": "t1",
            "historyId": "501",
            "payload": {
                "partId": "",
                "mimeType": "multipart/mixed",
                "headers": [{"name": "Subject", "value": "Snapshot"}],
                "body": {"size": 0},
                "parts": [
                    {
                        "partId": "0",
                        "mimeType": "text/plain",
                        "headers": [
                            {"name": "Content-Type", "value": "text/plain; charset=utf-8"}
                        ],
                        "body": {"size": 5, "data": _b64(b"hello")},
                    },
                    {
                        "partId": "1",
                        "mimeType": "application/octet-stream",
                        "filename": "data.bin",
                        "headers": [
                            {"name": "Content-Disposition", "value": "attachment; filename=data.bin"}
                        ],
                        "body": {"size": 3, "attachmentId": "a1"},
                    },
                ],
            },
        }

    def attachment(self) -> Mapping[str, Any]:
        self.attachment_calls += 1
        if self.fail_attachment:
            raise GmailApiError("temporary attachment failure")
        return {"attachmentId": "a1", "size": 3, "data": _b64(b"abc")}


class _Messages:
    def __init__(self, backend: _Backend) -> None:
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
        return {"messages": [{"id": "m1", "threadId": "t1"}]}

    def get_message(self, message_id: str, *, format: str = "full") -> Mapping[str, Any]:
        assert message_id == "m1"
        assert format == "full"
        return self.backend.message()


class _Attachments:
    def __init__(self, backend: _Backend) -> None:
        self.backend = backend

    def get_attachment(self, message_id: str, attachment_id: str) -> Mapping[str, Any]:
        assert message_id == "m1"
        assert attachment_id == "a1"
        return self.backend.attachment()


class _Client:
    def __init__(self, backend: _Backend) -> None:
        self.messages = _Messages(backend)
        self.attachments = _Attachments(backend)

    def get_profile(self) -> Mapping[str, Any]:
        return {
            "emailAddress": "user@example.com",
            "messagesTotal": 1,
            "threadsTotal": 1,
            "historyId": "500",
        }


def _factory(backend: _Backend):
    def factory(**kwargs: object) -> _Client:
        assert "credentials" in kwargs
        return _Client(backend)

    return factory


def test_metadata_only_snapshot_generates_mime_artifact_and_manifest(tmp_path: Path) -> None:
    provider = _Provider(tmp_path / "credentials")
    store = LocalArtifactStore(tmp_path / "artifacts")
    backend = _Backend()
    result = FullMessageSnapshot(
        provider,
        store,
        client_factory=_factory(backend),
        retry_sleep=lambda _: None,
    ).run()

    assert result.manifest_status == "COMPLETED"
    assert result.mime_messages_processed == 1
    assert result.mime_parts == 3
    assert result.attachments_detected == 1
    assert result.attachments_fetched == 0
    assert backend.attachment_calls == 0

    analysis_path = (
        store.run_root("gmail.messages", result.run_id)
        / "candidate"
        / "mime"
        / "m1.json"
    )
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    assert analysis["text_body"] == "hello"
    assert analysis["attachments"][0]["download_status"] == "METADATA_ONLY"

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["messages_fetched"] == 1
    assert manifest["attachments_detected"] == 1
    assert manifest["download_attachments"] is False


def test_downloaded_attachment_is_hashed_stored_and_not_refetched_on_resume(
    tmp_path: Path,
) -> None:
    provider = _Provider(tmp_path / "credentials")
    store = LocalArtifactStore(tmp_path / "artifacts")
    run_id = UUID("00000000-0000-0000-0000-000000000402")
    first_backend = _Backend()
    first = FullMessageSnapshot(
        provider,
        store,
        client_factory=_factory(first_backend),
        retry_sleep=lambda _: None,
    ).run(run_id=run_id, download_attachments=True)

    assert first.attachments_fetched == 1
    assert first.bytes_downloaded == 3
    assert first_backend.attachment_calls == 1
    binary_dir = store.run_root("gmail.attachments", run_id) / "raw"
    binaries = list(binary_dir.iterdir())
    assert len(binaries) == 1
    assert binaries[0].read_bytes() == b"abc"

    analysis_path = (
        store.run_root("gmail.messages", run_id) / "candidate" / "mime" / "m1.json"
    )
    analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
    assert analysis["attachments"][0]["content_hash_sha256"] == hashlib.sha256(b"abc").hexdigest()

    second_backend = _Backend()
    resumed = FullMessageSnapshot(
        provider,
        store,
        client_factory=_factory(second_backend),
        retry_sleep=lambda _: None,
    ).run(run_id=run_id, download_attachments=True)

    assert resumed.resumed is True
    assert resumed.attachments_fetched == 1
    assert second_backend.message_get_calls == 0
    assert second_backend.attachment_calls == 0
    assert len(list(binary_dir.iterdir())) == 1


def test_attachment_failure_is_partial_not_fatal(tmp_path: Path) -> None:
    provider = _Provider(tmp_path / "credentials")
    store = LocalArtifactStore(tmp_path / "artifacts")
    backend = _Backend(fail_attachment=True)
    result = FullMessageSnapshot(
        provider,
        store,
        client_factory=_factory(backend),
        retry_sleep=lambda _: None,
    ).run(download_attachments=True, max_attempts=2)

    assert result.manifest_status == "PARTIAL"
    assert result.attachments_failed == 1
    assert backend.attachment_calls == 2
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["status"] == "PARTIAL"
    assert manifest["errors"][0]["scope"] == "attachment"


def test_attachment_size_limit_skips_binary_without_api_call(tmp_path: Path) -> None:
    provider = _Provider(tmp_path / "credentials")
    store = LocalArtifactStore(tmp_path / "artifacts")
    backend = _Backend()
    result = FullMessageSnapshot(
        provider,
        store,
        client_factory=_factory(backend),
        retry_sleep=lambda _: None,
    ).run(download_attachments=True, max_attachment_bytes=2)

    assert result.manifest_status == "COMPLETED"
    assert result.attachments_detected == 1
    assert result.attachments_fetched == 0
    assert result.attachments_skipped == 1
    assert result.bytes_downloaded == 0
    assert backend.attachment_calls == 0
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["warnings"][0]["code"] == "ATTACHMENT_SIZE_LIMIT"


def test_resume_rejects_changed_attachment_settings(tmp_path: Path) -> None:
    provider = _Provider(tmp_path / "credentials")
    store = LocalArtifactStore(tmp_path / "artifacts")
    run_id = UUID("00000000-0000-0000-0000-000000000403")
    first_backend = _Backend()

    FullMessageSnapshot(
        provider,
        store,
        client_factory=_factory(first_backend),
        retry_sleep=lambda _: None,
    ).run(run_id=run_id, download_attachments=False)

    with pytest.raises(ValueError, match="download_attachments"):
        FullMessageSnapshot(
            provider,
            store,
            client_factory=_factory(_Backend()),
            retry_sleep=lambda _: None,
        ).run(run_id=run_id, download_attachments=True)
