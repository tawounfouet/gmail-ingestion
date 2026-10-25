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
ALL_HISTORY_TYPES = (
    "messageAdded",
    "messageDeleted",
    "labelAdded",
    "labelRemoved",
)


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
    assert result.duplicate_events_ignored == 0
    assert result.messages_hydrated == 2
    assert result.message_tombstones == 0
    assert result.label_mutations == 0
    assert checkpoints.load_committed(ACCOUNT_ID) == result.committed_checkpoint
    assert checkpoints.load_candidate(ACCOUNT_ID, RUN_ID) is None

    assert [call["start_history_id"] for call in history.calls] == ["100", "100"]
    assert [call["page_token"] for call in history.calls] == [None, "p2"]
    assert all(call["history_types"] == ALL_HISTORY_TYPES for call in history.calls)
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
    assert manifest["history_types"] == list(ALL_HISTORY_TYPES)
    assert manifest["message_format"] == "full"


def test_incremental_reduces_deletions_and_label_mutations(tmp_path: Path) -> None:
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
                        "messagesDeleted": [
                            {"message": {"id": "m2", "threadId": "t2"}}
                        ],
                        "labelsAdded": [
                            {
                                "message": {"id": "m1", "threadId": "t1"},
                                "labelIds": ["INBOX", "Label_1"],
                            }
                        ],
                    },
                    {
                        "id": "102",
                        "labelsRemoved": [
                            {
                                "message": {"id": "m1", "threadId": "t1"},
                                "labelIds": ["INBOX"],
                            }
                        ],
                    },
                ],
                "historyId": "130",
            }
        }
    )
    messages = _Messages({"m1": _message("m1", "t1")})

    result = IncrementalHistorySync(
        _Provider(),
        artifacts,
        checkpoints,
        client_factory=_factory(history, messages),
    ).run(run_id=RUN_ID)

    assert result.events_persisted == 5
    assert result.messages_hydrated == 1
    assert result.message_tombstones == 1
    assert result.label_mutations == 3
    assert result.output_history_id == "130"

    history_root = artifacts.run_root("gmail.history", RUN_ID)
    event_paths = list((history_root / "history_events").glob("*.json"))
    assert len(event_paths) == 5
    assert {
        json.loads(path.read_text(encoding="utf-8"))["processing_status"]
        for path in event_paths
    } == {"PROCESSED"}

    tombstone_paths = list((history_root / "message_tombstones").glob("*.json"))
    mutation_paths = list((history_root / "message_label_mutations").glob("*.json"))
    assert len(tombstone_paths) == 1
    assert len(mutation_paths) == 3

    tombstone = json.loads(tombstone_paths[0].read_text(encoding="utf-8"))
    assert tombstone["message_id"] == "m2"
    assert tombstone["deletion_history_id"] == "101"

    mutations = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in mutation_paths
    ]
    assert {
        (mutation["label_id"], mutation["operation"], mutation["is_active"])
        for mutation in mutations
    } == {
        ("INBOX", "UPSERT_ACTIVE", True),
        ("Label_1", "UPSERT_ACTIVE", True),
        ("INBOX", "DEACTIVATE", False),
    }

    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["message_tombstones"] == 1
    assert manifest["label_mutations"] == 3


def test_duplicate_history_event_is_reduced_once_per_run(tmp_path: Path) -> None:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    checkpoints = LocalCheckpointStore(tmp_path / "checkpoints")
    _seed_checkpoint(checkpoints)

    duplicate_record = {
        "id": "101",
        "messagesDeleted": [{"message": {"id": "m9", "threadId": "t9"}}],
    }
    history = _History(
        {
            None: {
                "history": [duplicate_record],
                "nextPageToken": "p2",
                "historyId": "105",
            },
            "p2": {
                "history": [duplicate_record],
                "historyId": "110",
            },
        }
    )

    result = IncrementalHistorySync(
        _Provider(),
        artifacts,
        checkpoints,
        client_factory=_factory(history, _Messages({})),
    ).run(run_id=RUN_ID)

    assert result.events_persisted == 1
    assert result.duplicate_events_ignored == 1
    assert result.message_tombstones == 1
    assert len(
        list(
            (
                artifacts.run_root("gmail.history", RUN_ID)
                / "message_tombstones"
            ).glob("*.json")
        )
    ) == 1


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
    assert result.message_tombstones == 0
    assert result.label_mutations == 0
    assert result.output_history_id == "130"
    committed = checkpoints.load_committed(ACCOUNT_ID)
    assert committed is not None
    assert committed.history_id == "130"


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

    history_root = artifacts.run_root("gmail.history", RUN_ID)
    event_paths = list((history_root / "history_events").glob("*.json"))
    assert len(event_paths) == 1
    pending_event = json.loads(event_paths[0].read_text(encoding="utf-8"))
    assert pending_event["processing_status"] == "PENDING"
    assert pending_event["processed_at"] is None

    manifest_path = history_root / "incremental_manifest.json"
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
