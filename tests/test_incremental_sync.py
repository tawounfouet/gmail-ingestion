import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.api import GmailApiError, GmailHistoryExpiredError
from gmail_ingestion.application import (
    FullResyncRequiredError,
    IncrementalHistorySync,
    MissingCommittedCheckpointError,
)
from gmail_ingestion.domain import GmailCheckpoint
from gmail_ingestion.persistence import LocalCheckpointStore

ACCOUNT_ID = "acct_0123456789abcdef01234567"
SEED_RUN = UUID("00000000-0000-0000-0000-000000000420")
RUN_ID = UUID("00000000-0000-0000-0000-000000000421")
T0 = datetime(2026, 10, 25, 13, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 25, 13, 1, tzinfo=UTC)


class _Provider:
    def load(self, account_id: str | None = None) -> tuple[str, object]:
        assert account_id in {None, ACCOUNT_ID}
        return ACCOUNT_ID, object()


class _History:
    def __init__(
        self,
        pages: dict[str | None, dict[str, Any]],
        *,
        expired: bool = False,
    ) -> None:
        self.pages = pages
        self.expired = expired
        self.calls: list[dict[str, Any]] = []

    def list_history(
        self,
        *,
        start_history_id: str,
        page_token: str | None = None,
        max_results: int = 500,
        history_types: tuple[str, ...] = (),
        label_id: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "start_history_id": start_history_id,
                "page_token": page_token,
                "max_results": max_results,
                "history_types": history_types,
                "label_id": label_id,
            }
        )
        if self.expired:
            raise GmailHistoryExpiredError("expired")
        return self.pages[page_token]


class _Messages:
    def __init__(
        self,
        payloads: dict[str, dict[str, Any]],
        *,
        fail_message_id: str | None = None,
    ) -> None:
        self.payloads = payloads
        self.fail_message_id = fail_message_id
        self.calls: list[tuple[str, str]] = []

    def get_message(self, message_id: str, *, format: str = "full") -> dict[str, Any]:
        self.calls.append((message_id, format))
        if message_id == self.fail_message_id:
            raise GmailApiError(f"unable to hydrate {message_id}")
        return self.payloads[message_id]


class _Client:
    def __init__(self, history: _History, messages: _Messages) -> None:
        self.history = history
        self.messages = messages


def _factory(history: _History, messages: _Messages):
    def factory(**kwargs: object) -> _Client:
        assert "credentials" in kwargs
        return _Client(history, messages)

    return factory


def _seed_checkpoint(store: LocalCheckpointStore, history_id: str = "100") -> None:
    candidate = GmailCheckpoint.candidate(
        account_id=ACCOUNT_ID,
        history_id=history_id,
        run_id=SEED_RUN,
        created_at=T0,
    )
    store.save_candidate(candidate)
    store.commit(candidate, committed_at=T1)


def _message(message_id: str, thread_id: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "threadId": thread_id,
        "historyId": "125",
        "labelIds": ["INBOX"],
        "payload": {"mimeType": "text/plain", "headers": []},
    }


def test_incremental_paginates_hydrates_persists_then_commits(tmp_path: Path) -> None:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    checkpoints = LocalCheckpointStore(tmp_path / "checkpoints")
    _seed_checkpoint(checkpoints)

    history = _History(
        {
            None: {
                "history": [
                    {
                        "id": "101",
                        "messagesAdded": [
                            {"message": {"id": "m1", "threadId": "t1"}}
                        ],
                    }
                ],
                "nextPageToken": "p2",
                "historyId": "110",
            },
            "p2": {
                "history": [
                    {
                        "id": "120",
                        "messagesAdded": [
                            {"message": {"id": "m2", "threadId": "t2"}}
                        ],
                    }
                ],
                "historyId": "125",
            },
        }
    )
    messages = _Messages(
        {
            "m1": _message("m1", "t1"),
            "m2": _message("m2", "t2"),
        }
    )

    result = IncrementalHistorySync(
        _Provider(),
        artifacts,
        checkpoints,
        client_factory=_factory(history, messages),
    ).run(run_id=RUN_ID, max_results=100)

    assert result.input_history_id == "100"
    assert result.output_history_id == "125"
    assert result.pages_scanned == 2
    assert result.events_persisted == 2
    assert result.messages_hydrated == 2
    assert checkpoints.load_committed(ACCOUNT_ID) == result.committed_checkpoint
    assert checkpoints.load_candidate(ACCOUNT_ID, RUN_ID) is None

    assert [call["start_history_id"] for call in history.calls] == ["100", "100"]
    assert [call["page_token"] for call in history.calls] == [None, "p2"]
    assert all(call["history_types"] == ("messageAdded",) for call in history.calls)
    assert messages.calls == [("m1", "full"), ("m2", "full")]

    history_root = artifacts.run_root("gmail.history", RUN_ID)
    assert len(list((history_root / "raw").glob("history-page-*.json"))) == 2
    assert len(list((history_root / "history_events").glob("*.json"))) == 2

    message_root = artifacts.run_root("gmail.messages", RUN_ID) / "raw"
    assert len(list(message_root.glob("message-*-full.json"))) == 2

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["status"] == "COMPLETED"
    assert manifest["input_checkpoint"] == "100"
    assert manifest["candidate_checkpoint"] == "125"
    assert manifest["committed_checkpoint"] == "125"
    assert manifest["history_types"] == ["messageAdded"]
    assert manifest["message_format"] == "full"


def test_incremental_advances_checkpoint_when_no_events_exist(tmp_path: Path) -> None:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    checkpoints = LocalCheckpointStore(tmp_path / "checkpoints")
    _seed_checkpoint(checkpoints)

    history = _History({None: {"historyId": "130"}})
    messages = _Messages({})

    result = IncrementalHistorySync(
        _Provider(),
        artifacts,
        checkpoints,
        client_factory=_factory(history, messages),
    ).run(run_id=RUN_ID)

    assert result.events_persisted == 0
    assert result.messages_hydrated == 0
    assert result.output_history_id == "130"
    assert checkpoints.load_committed(ACCOUNT_ID).history_id == "130"


def test_hydration_failure_never_advances_committed_checkpoint(tmp_path: Path) -> None:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    checkpoints = LocalCheckpointStore(tmp_path / "checkpoints")
    _seed_checkpoint(checkpoints)

    history = _History(
        {
            None: {
                "history": [
                    {
                        "id": "101",
                        "messagesAdded": [
                            {"message": {"id": "m1", "threadId": "t1"}}
                        ],
                    }
                ],
                "historyId": "125",
            }
        }
    )
    messages = _Messages(
        {"m1": _message("m1", "t1")},
        fail_message_id="m1",
    )

    with pytest.raises(GmailApiError):
        IncrementalHistorySync(
            _Provider(),
            artifacts,
            checkpoints,
            client_factory=_factory(history, messages),
        ).run(run_id=RUN_ID)

    committed = checkpoints.load_committed(ACCOUNT_ID)
    assert committed is not None
    assert committed.history_id == "100"
    assert checkpoints.load_candidate(ACCOUNT_ID, RUN_ID) is None

    manifest_path = artifacts.run_root("gmail.history", RUN_ID) / "incremental_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "FAILED"
    assert manifest["committed_checkpoint"] is None


def test_expired_history_invalidates_checkpoint_and_requires_full_resync(
    tmp_path: Path,
) -> None:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    checkpoints = LocalCheckpointStore(tmp_path / "checkpoints")
    _seed_checkpoint(checkpoints)

    history = _History({}, expired=True)
    messages = _Messages({})

    with pytest.raises(FullResyncRequiredError, match="full sync"):
        IncrementalHistorySync(
            _Provider(),
            artifacts,
            checkpoints,
            client_factory=_factory(history, messages),
        ).run(run_id=RUN_ID)

    assert checkpoints.load_committed(ACCOUNT_ID) is None
    invalid = checkpoints.load_invalid(ACCOUNT_ID)
    assert invalid is not None
    assert invalid.history_id == "100"
    assert invalid.invalid_reason is not None

    manifest_path = artifacts.run_root("gmail.history", RUN_ID) / "incremental_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "FULL_RESYNC_REQUIRED"


def test_incremental_requires_existing_committed_checkpoint(tmp_path: Path) -> None:
    with pytest.raises(MissingCommittedCheckpointError):
        IncrementalHistorySync(
            _Provider(),
            LocalArtifactStore(tmp_path / "artifacts"),
            LocalCheckpointStore(tmp_path / "checkpoints"),
            client_factory=_factory(_History({}), _Messages({})),
        ).run(run_id=RUN_ID)
