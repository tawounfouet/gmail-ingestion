from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.api.messages import GmailMessagesApi
from gmail_ingestion.application import (
    FilteredMessageSnapshot,
    FullMessageSnapshot,
    RangeMessageSnapshot,
)
from gmail_ingestion.auth.store import LocalCredentialStore
from gmail_ingestion.domain import DateRange, GmailAccount, GmailFilter


class _Request:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def execute(self) -> dict[str, object]:
        return self.payload


class _RecordingMessagesResource:
    def __init__(self) -> None:
        self.list_kwargs: dict[str, Any] | None = None

    def list(self, **kwargs: Any) -> _Request:
        self.list_kwargs = kwargs
        return _Request({"messages": []})


class _UsersResource:
    def __init__(self, messages: _RecordingMessagesResource) -> None:
        self._messages = messages

    def messages(self) -> _RecordingMessagesResource:
        return self._messages


class _Service:
    def __init__(self, messages: _RecordingMessagesResource) -> None:
        self._users = _UsersResource(messages)

    def users(self) -> _UsersResource:
        return self._users


class _Provider:
    def __init__(self, root: Path) -> None:
        self.store = LocalCredentialStore(root)
        self.credentials = object()
        self.account_id = self.store.find_or_create_account_id("user@example.com")
        self.store.save_account(
            GmailAccount(self.account_id, "user@example.com", 2, 2, "100")
        )
        self.store.set_active_account(self.account_id)

    def load(self, account_id: str | None = None) -> tuple[str, object]:
        return self.store.resolve_account_id(account_id), self.credentials


class _Backend:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def page(
        self,
        *,
        page_token: str | None,
        query: str | None,
        label_ids: Sequence[str],
        include_spam_trash: bool,
    ) -> Mapping[str, Any]:
        self.calls.append(
            {
                "page_token": page_token,
                "query": query,
                "label_ids": tuple(label_ids),
                "include_spam_trash": include_spam_trash,
            }
        )
        if page_token is None:
            return {
                "messages": [{"id": "m1", "threadId": "t1"}],
                "nextPageToken": "p2",
            }
        return {"messages": [{"id": "m2", "threadId": "t2"}]}


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
        return self.backend.page(
            page_token=page_token,
            query=query,
            label_ids=label_ids,
            include_spam_trash=include_spam_trash,
        )

    def get_message(self, message_id: str, *, format: str = "full") -> Mapping[str, Any]:
        assert format == "full"
        return {
            "id": message_id,
            "threadId": f"thread-{message_id}",
            "historyId": "101",
            "payload": {"mimeType": "text/plain"},
        }


class _Client:
    def __init__(self, backend: _Backend) -> None:
        self.messages = _Messages(backend)

    def get_profile(self) -> Mapping[str, Any]:
        return {
            "emailAddress": "user@example.com",
            "messagesTotal": 2,
            "threadsTotal": 2,
            "historyId": "100",
        }


def _factory(backend: _Backend):
    def factory(**kwargs: object) -> _Client:
        assert "credentials" in kwargs
        return _Client(backend)

    return factory


def test_gmail_filter_is_deterministic_and_preserves_requested_values() -> None:
    left = GmailFilter.from_values(
        query="has:attachment",
        label_ids=("IMPORTANT", "INBOX", "IMPORTANT"),
        include_spam_trash=True,
    )
    right = GmailFilter.from_values(
        query="has:attachment",
        label_ids=("INBOX", "IMPORTANT"),
        include_spam_trash=True,
    )

    assert left.label_ids == ("IMPORTANT", "INBOX", "IMPORTANT")
    assert left.canonical_label_ids == ("IMPORTANT", "INBOX")
    assert left.requested_payload()["label_ids"] == ["IMPORTANT", "INBOX", "IMPORTANT"]
    assert left.effective_payload()["label_ids"] == ["IMPORTANT", "INBOX"]
    assert left.scope_fingerprint == right.scope_fingerprint
    assert left.scope_kind == "filtered"


@pytest.mark.parametrize(
    ("query", "label_ids"),
    [
        ("   ", ()),
        (None, ("",)),
        (None, (" IMPORTANT ",)),
    ],
)
def test_gmail_filter_rejects_ambiguous_values(
    query: str | None,
    label_ids: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError):
        GmailFilter.from_values(query=query, label_ids=label_ids)


def test_messages_api_forwards_q_labels_and_spam_trash() -> None:
    resource = _RecordingMessagesResource()
    api = GmailMessagesApi(_Service(resource))

    api.list_messages(
        page_token="p2",
        max_results=100,
        include_spam_trash=True,
        query="in:inbox is:unread",
        label_ids=("INBOX", "IMPORTANT"),
    )

    assert resource.list_kwargs == {
        "userId": "me",
        "maxResults": 100,
        "includeSpamTrash": True,
        "pageToken": "p2",
        "q": "in:inbox is:unread",
        "labelIds": ["INBOX", "IMPORTANT"],
    }


def test_filtered_snapshot_preserves_scope_across_pagination_and_manifest(
    tmp_path: Path,
) -> None:
    provider = _Provider(tmp_path / "credentials")
    store = LocalArtifactStore(tmp_path / "artifacts")
    backend = _Backend()
    run_id = UUID("00000000-0000-0000-0000-000000000307")
    gmail_filter = GmailFilter.from_values(
        query="has:attachment",
        label_ids=("IMPORTANT", "INBOX"),
        include_spam_trash=True,
    )

    result = FilteredMessageSnapshot(
        provider,
        store,
        client_factory=_factory(backend),
        retry_sleep=lambda _: None,
    ).run(gmail_filter, run_id=run_id)

    assert result.discovered_count == 2
    assert len(backend.calls) == 2
    assert all(call["query"] == "has:attachment" for call in backend.calls)
    assert all(call["label_ids"] == ("IMPORTANT", "INBOX") for call in backend.calls)
    assert all(call["include_spam_trash"] is True for call in backend.calls)

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["mode"] == "filtered_snapshot"
    assert manifest["scope"]["kind"] == "filtered"
    assert manifest["scope"]["fingerprint"] == gmail_filter.scope_fingerprint
    assert manifest["requested_filter"] == {
        "query": "has:attachment",
        "label_ids": ["IMPORTANT", "INBOX"],
        "include_spam_trash": True,
        "after": None,
        "before": None,
        "last_days": None,
    }
    assert manifest["effective_filter"] == {
        "gmail_q": "has:attachment",
        "label_ids": ["IMPORTANT", "INBOX"],
        "include_spam_trash": True,
        "resolved_after": None,
        "resolved_before": None,
        "resolution_timestamp": None,
    }


def test_resume_rejects_changed_label_scope(tmp_path: Path) -> None:
    provider = _Provider(tmp_path / "credentials")
    store = LocalArtifactStore(tmp_path / "artifacts")
    run_id = UUID("00000000-0000-0000-0000-000000000308")

    first = FullMessageSnapshot(
        provider,
        store,
        client_factory=_factory(_Backend()),
        retry_sleep=lambda _: None,
    )
    first.run(run_id=run_id, query="has:attachment", label_ids=("INBOX",))

    resumed = FullMessageSnapshot(
        provider,
        store,
        client_factory=_factory(_Backend()),
        retry_sleep=lambda _: None,
    )
    with pytest.raises(ValueError, match="label_ids|scope"):
        resumed.run(run_id=run_id, query="has:attachment", label_ids=("IMPORTANT",))


def test_filtered_use_case_rejects_unfiltered_scope(tmp_path: Path) -> None:
    provider = _Provider(tmp_path / "credentials")
    filtered = FilteredMessageSnapshot(
        provider,
        LocalArtifactStore(tmp_path / "artifacts"),
        client_factory=_factory(_Backend()),
        retry_sleep=lambda _: None,
    )

    with pytest.raises(ValueError, match="requires query and/or label_ids"):
        filtered.run(GmailFilter())



def test_date_range_compiles_exact_gmail_query() -> None:
    gmail_filter = GmailFilter.from_values(
        query="has:attachment",
        label_ids=("IMPORTANT",),
        after=date(2026, 10, 1),
        before=date(2026, 10, 24),
    )

    assert gmail_filter.scope_kind == "range"
    assert (
        gmail_filter.effective_query
        == "has:attachment after:2026/10/01 before:2026/10/24"
    )
    assert gmail_filter.requested_payload()["after"] == "2026-10-01"
    assert gmail_filter.requested_payload()["before"] == "2026-10-24"
    assert gmail_filter.effective_payload()["resolved_after"] == "2026-10-01"
    assert gmail_filter.effective_payload()["resolved_before"] == "2026-10-24"


def test_last_days_is_frozen_against_resolution_timestamp() -> None:
    resolved_at = datetime(2026, 10, 25, 9, 30, tzinfo=UTC)
    gmail_filter = GmailFilter.from_values(
        last_days=7,
        resolution_timestamp=resolved_at,
    )

    assert gmail_filter.effective_query == "after:2026/10/18"
    assert gmail_filter.requested_payload()["last_days"] == 7
    assert gmail_filter.effective_payload()["resolved_after"] == "2026-10-18"
    assert (
        gmail_filter.effective_payload()["resolution_timestamp"]
        == "2026-10-25T09:30:00+00:00"
    )


@pytest.mark.parametrize(
    "date_range",
    [
        lambda: DateRange(after=date(2026, 10, 24), before=date(2026, 10, 1)),
        lambda: DateRange(last_days=0),
        lambda: DateRange(after=date(2026, 10, 1), last_days=7),
    ],
)
def test_date_range_rejects_invalid_inputs(date_range: Any) -> None:
    with pytest.raises(ValueError):
        date_range()


def test_range_snapshot_combines_query_labels_dates_and_manifest(tmp_path: Path) -> None:
    provider = _Provider(tmp_path / "credentials")
    store = LocalArtifactStore(tmp_path / "artifacts")
    backend = _Backend()
    run_id = UUID("00000000-0000-0000-0000-000000000309")

    result = RangeMessageSnapshot(
        provider,
        store,
        client_factory=_factory(backend),
        retry_sleep=lambda _: None,
    ).run(
        DateRange(after=date(2026, 10, 1), before=date(2026, 10, 24)),
        run_id=run_id,
        query="has:attachment",
        label_ids=("IMPORTANT",),
    )

    expected_q = "has:attachment after:2026/10/01 before:2026/10/24"
    assert len(backend.calls) == 2
    assert all(call["query"] == expected_q for call in backend.calls)
    assert all(call["label_ids"] == ("IMPORTANT",) for call in backend.calls)

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["mode"] == "range_snapshot"
    assert manifest["scope"]["kind"] == "range"
    assert manifest["requested_filter"]["after"] == "2026-10-01"
    assert manifest["requested_filter"]["before"] == "2026-10-24"
    assert manifest["effective_filter"]["gmail_q"] == expected_q
    assert manifest["effective_filter"]["resolved_after"] == "2026-10-01"
    assert manifest["effective_filter"]["resolved_before"] == "2026-10-24"


def test_last_days_resume_reuses_original_frozen_scope(tmp_path: Path) -> None:
    provider = _Provider(tmp_path / "credentials")
    store = LocalArtifactStore(tmp_path / "artifacts")
    run_id = UUID("00000000-0000-0000-0000-000000000310")

    first = FullMessageSnapshot(
        provider,
        store,
        client_factory=_factory(_Backend()),
        retry_sleep=lambda _: None,
    ).run(run_id=run_id, last_days=7)
    first_manifest = json.loads(Path(first.manifest_path).read_text(encoding="utf-8"))

    second = FullMessageSnapshot(
        provider,
        store,
        client_factory=_factory(_Backend()),
        retry_sleep=lambda _: None,
    ).run(run_id=run_id, last_days=7)
    second_manifest = json.loads(Path(second.manifest_path).read_text(encoding="utf-8"))

    assert second.resumed is True
    assert (
        second_manifest["effective_filter"]["resolved_after"]
        == first_manifest["effective_filter"]["resolved_after"]
    )
    assert (
        second_manifest["effective_filter"]["resolution_timestamp"]
        == first_manifest["effective_filter"]["resolution_timestamp"]
    )
    assert second_manifest["scope"]["fingerprint"] == first_manifest["scope"]["fingerprint"]


def test_resume_rejects_changed_relative_range(tmp_path: Path) -> None:
    provider = _Provider(tmp_path / "credentials")
    store = LocalArtifactStore(tmp_path / "artifacts")
    run_id = UUID("00000000-0000-0000-0000-000000000311")

    FullMessageSnapshot(
        provider,
        store,
        client_factory=_factory(_Backend()),
        retry_sleep=lambda _: None,
    ).run(run_id=run_id, last_days=7)

    with pytest.raises(ValueError, match="last_days|scope"):
        FullMessageSnapshot(
            provider,
            store,
            client_factory=_factory(_Backend()),
            retry_sleep=lambda _: None,
        ).run(run_id=run_id, last_days=30)
