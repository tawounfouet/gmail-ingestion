from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import UUID

from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.api import GmailApiError
from gmail_ingestion.application import FullMessageSnapshot
from gmail_ingestion.auth.store import LocalCredentialStore
from gmail_ingestion.domain import GmailAccount


class _Provider:
    def __init__(self, root: Path) -> None:
        self.store = LocalCredentialStore(root)
        self.credentials = object()
        self.account_id = self.store.find_or_create_account_id("user@example.com")
        self.store.save_account(
            GmailAccount(self.account_id, "user@example.com", 3, 2, "100")
        )
        self.store.set_active_account(self.account_id)

    def load(self, account_id: str | None = None) -> tuple[str, object]:
        return self.store.resolve_account_id(account_id), self.credentials


class _Backend:
    def __init__(
        self,
        pages: dict[str | None, dict[str, Any]],
        *,
        failures_before_success: dict[str, int] | None = None,
    ) -> None:
        self.pages = pages
        self.failures_before_success = failures_before_success or {}
        self.fetch_calls: dict[str, int] = {}
        self.list_calls = 0
        self.list_tokens: list[str | None] = []
        self.list_queries: list[str | None] = []
        self.lock = Lock()

    def list_page(
        self,
        page_token: str | None,
        query: str | None = None,
    ) -> Mapping[str, Any]:
        with self.lock:
            self.list_calls += 1
            self.list_tokens.append(page_token)
            self.list_queries.append(query)
        page = self.pages[page_token]
        if isinstance(page, Exception):
            raise page
        return page

    def get_message(self, message_id: str) -> Mapping[str, Any]:
        with self.lock:
            calls = self.fetch_calls.get(message_id, 0) + 1
            self.fetch_calls[message_id] = calls
            failures = self.failures_before_success.get(message_id, 0)
        if calls <= failures:
            raise GmailApiError(f"temporary failure for {message_id}")
        return {
            "id": message_id,
            "threadId": f"thread-{message_id}",
            "historyId": "101",
            "payload": {"mimeType": "text/plain"},
        }


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
        assert max_results == 500
        assert include_spam_trash is False
        assert label_ids == ()
        return self.backend.list_page(page_token, query)

    def get_message(self, message_id: str, *, format: str = "full") -> Mapping[str, Any]:
        assert format == "full"
        return self.backend.get_message(message_id)


class _Client:
    def __init__(self, backend: _Backend) -> None:
        self.messages = _Messages(backend)

    def get_profile(self) -> Mapping[str, Any]:
        return {
            "emailAddress": "user@example.com",
            "messagesTotal": 3,
            "threadsTotal": 2,
            "historyId": "100",
        }


def _factory(backend: _Backend):
    def factory(**kwargs: object) -> _Client:
        assert "credentials" in kwargs
        return _Client(backend)

    return factory


def test_snapshot_handles_zero_messages(tmp_path: Path) -> None:
    provider = _Provider(tmp_path / "credentials")
    backend = _Backend({None: {"messages": [], "resultSizeEstimate": 0}})
    result = FullMessageSnapshot(
        provider,
        LocalArtifactStore(tmp_path / "artifacts"),
        client_factory=_factory(backend),
        retry_sleep=lambda _: None,
    ).run()

    assert result.discovered_count == 0
    assert result.fetched_count == 0
    assert result.failed_count == 0
    assert result.pages_scanned == 1



def test_snapshot_hydrates_one_message_to_immutable_raw(tmp_path: Path) -> None:
    provider = _Provider(tmp_path / "credentials")
    store = LocalArtifactStore(tmp_path / "artifacts")
    run_id = UUID("00000000-0000-0000-0000-000000000302")
    backend = _Backend(
        {
            None: {
                "messages": [{"id": "m1", "threadId": "t1"}],
                "resultSizeEstimate": 1,
            }
        }
    )

    result = FullMessageSnapshot(
        provider,
        store,
        client_factory=_factory(backend),
        retry_sleep=lambda _: None,
    ).run(run_id=run_id)

    assert result.discovered_count == 1
    assert result.fetched_count == 1
    assert result.failed_count == 0
    raw_path = (
        store.run_root("gmail.messages", run_id)
        / "raw"
        / "message-m1-full.json"
    )
    assert raw_path.is_file()
    assert json.loads(raw_path.read_text(encoding="utf-8"))["id"] == "m1"

def test_snapshot_paginates_deduplicates_and_retries(tmp_path: Path) -> None:
    provider = _Provider(tmp_path / "credentials")
    backend = _Backend(
        {
            None: {
                "messages": [
                    {"id": "m1", "threadId": "t1"},
                    {"id": "m2", "threadId": "t2"},
                ],
                "nextPageToken": "p2",
            },
            "p2": {
                "messages": [
                    {"id": "m2", "threadId": "t2"},
                    {"id": "m3", "threadId": "t3"},
                ]
            },
        },
        failures_before_success={"m3": 1},
    )
    result = FullMessageSnapshot(
        provider,
        LocalArtifactStore(tmp_path / "artifacts"),
        client_factory=_factory(backend),
        retry_sleep=lambda _: None,
    ).run(max_workers=2, max_attempts=3)

    assert result.pages_scanned == 2
    assert result.discovered_count == 3
    assert result.fetched_count == 3
    assert result.failed_count == 0
    assert result.raw_message_count == 3
    assert backend.fetch_calls["m3"] == 2


def test_snapshot_partial_failure_then_resume_without_duplicates(tmp_path: Path) -> None:
    provider = _Provider(tmp_path / "credentials")
    store = LocalArtifactStore(tmp_path / "artifacts")
    run_id = UUID("00000000-0000-0000-0000-000000000303")
    first_backend = _Backend(
        {
            None: {
                "messages": [
                    {"id": "m1", "threadId": "t1"},
                    {"id": "m2", "threadId": "t2"},
                ]
            }
        },
        failures_before_success={"m2": 10},
    )
    first = FullMessageSnapshot(
        provider,
        store,
        client_factory=_factory(first_backend),
        retry_sleep=lambda _: None,
    ).run(run_id=run_id, max_attempts=2)

    assert first.fetched_count == 1
    assert first.failed_count == 1

    second_backend = _Backend({None: {"messages": []}})
    resumed = FullMessageSnapshot(
        provider,
        store,
        client_factory=_factory(second_backend),
        retry_sleep=lambda _: None,
    ).run(run_id=run_id, max_attempts=2)

    assert resumed.resumed is True
    assert resumed.discovered_count == 2
    assert resumed.fetched_count == 2
    assert resumed.failed_count == 0
    assert second_backend.list_calls == 0
    assert second_backend.fetch_calls == {"m2": 1}
    raw_dir = store.run_root("gmail.messages", run_id) / "raw"
    assert len(list(raw_dir.glob("message-*-full.json"))) == 2


def test_snapshot_resumes_discovery_from_persisted_page_token(tmp_path: Path) -> None:
    provider = _Provider(tmp_path / "credentials")
    store = LocalArtifactStore(tmp_path / "artifacts")
    run_id = UUID("00000000-0000-0000-0000-000000000304")

    first_backend = _Backend(
        {
            None: {
                "messages": [{"id": "m1", "threadId": "t1"}],
                "nextPageToken": "p2",
            },
            "p2": GmailApiError("temporary discovery failure"),
        }
    )

    try:
        FullMessageSnapshot(
            provider,
            store,
            client_factory=_factory(first_backend),
            retry_sleep=lambda _: None,
        ).run(run_id=run_id)
    except GmailApiError:
        pass
    else:
        raise AssertionError("expected discovery failure")

    second_backend = _Backend(
        {
            "p2": {
                "messages": [{"id": "m2", "threadId": "t2"}],
            }
        }
    )
    resumed = FullMessageSnapshot(
        provider,
        store,
        client_factory=_factory(second_backend),
        retry_sleep=lambda _: None,
    ).run(run_id=run_id)

    assert resumed.resumed is True
    assert resumed.discovered_count == 2
    assert resumed.fetched_count == 2
    assert second_backend.list_tokens == ["p2"]


def test_snapshot_qualification_limit_bounds_real_scope(tmp_path: Path) -> None:
    provider = _Provider(tmp_path / "credentials")
    store = LocalArtifactStore(tmp_path / "artifacts")
    run_id = UUID("00000000-0000-0000-0000-000000000305")
    backend = _Backend(
        {
            None: {
                "messages": [
                    {"id": "m1", "threadId": "t1"},
                    {"id": "m2", "threadId": "t2"},
                    {"id": "m3", "threadId": "t3"},
                ],
                "nextPageToken": "p2",
            }
        }
    )

    result = FullMessageSnapshot(
        provider,
        store,
        client_factory=_factory(backend),
        retry_sleep=lambda _: None,
    ).run(run_id=run_id, message_limit=2)

    assert result.discovered_count == 2
    assert result.fetched_count == 2
    assert set(backend.fetch_calls) == {"m1", "m2"}
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["mode"] == "sample_snapshot"
    assert manifest["message_limit"] == 2


def test_hydration_checkpoints_are_batched(tmp_path: Path, monkeypatch) -> None:
    from gmail_ingestion.control import MessageDiscoveryQueue

    provider = _Provider(tmp_path / "credentials")
    messages = [
        {"id": f"m{index}", "threadId": f"t{index}"}
        for index in range(10)
    ]
    backend = _Backend({None: {"messages": messages}})
    persist_calls = 0
    original_persist = MessageDiscoveryQueue.persist

    def counted_persist(self):
        nonlocal persist_calls
        persist_calls += 1
        return original_persist(self)

    monkeypatch.setattr(MessageDiscoveryQueue, "persist", counted_persist)

    result = FullMessageSnapshot(
        provider,
        LocalArtifactStore(tmp_path / "artifacts"),
        client_factory=_factory(backend),
        retry_sleep=lambda _: None,
        queue_checkpoint_batch_size=3,
    ).run(max_workers=2)

    assert result.fetched_count == 10
    assert persist_calls == 5


def test_snapshot_query_is_forwarded_recorded_and_resume_guarded(tmp_path: Path) -> None:
    provider = _Provider(tmp_path / "credentials")
    store = LocalArtifactStore(tmp_path / "artifacts")
    run_id = UUID("00000000-0000-0000-0000-000000000306")
    backend = _Backend(
        {
            None: {
                "messages": [{"id": "m1", "threadId": "t1"}],
            }
        }
    )

    result = FullMessageSnapshot(
        provider,
        store,
        client_factory=_factory(backend),
        retry_sleep=lambda _: None,
    ).run(run_id=run_id, message_limit=1, query="has:attachment")

    assert result.fetched_count == 1
    assert backend.list_queries == ["has:attachment"]
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["filters"]["query"] == "has:attachment"

    try:
        FullMessageSnapshot(
            provider,
            store,
            client_factory=_factory(_Backend({None: {"messages": []}})),
            retry_sleep=lambda _: None,
        ).run(run_id=run_id, message_limit=1, query="is:unread")
    except ValueError as exc:
        assert "query" in str(exc)
    else:
        raise AssertionError("expected resume query mismatch")
