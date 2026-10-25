import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.api import GmailApiError, GmailHistoryExpiredError
from gmail_ingestion.application import (
    FullSnapshotRecoveryError,
    IncrementalHistorySync,
    ReliableIncrementalSync,
)
from gmail_ingestion.domain import GmailCheckpoint, map_history_events
from gmail_ingestion.persistence import (
    CheckpointConflictError,
    HistoryEventWriter,
    IncrementalManifestWriter,
    LocalCheckpointStore,
    LocalHistoryReceiptStore,
    RawHistoryWriter,
    RawMessageWriter,
)

ACCOUNT_ID = "acct_0123456789abcdef01234567"
SEED_RUN = UUID("00000000-0000-0000-0000-000000000610")
RUN_ID = UUID("00000000-0000-0000-0000-000000000611")
RUN_B = UUID("00000000-0000-0000-0000-000000000612")
T0 = datetime(2026, 10, 25, 14, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 25, 14, 1, tzinfo=UTC)


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


def _sequence_factory(clients: list[_Client]):
    remaining = list(clients)

    def factory(**kwargs: object) -> _Client:
        assert "credentials" in kwargs
        assert remaining
        return remaining.pop(0)

    return factory


def _seed(store: LocalCheckpointStore, history_id: str = "100") -> None:
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
        "historyId": "150",
        "labelIds": ["INBOX"],
        "payload": {"mimeType": "text/plain", "headers": []},
    }


def test_same_run_restart_replays_frozen_pages_and_finishes_pending_event(
    tmp_path: Path,
) -> None:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    checkpoints = LocalCheckpointStore(tmp_path / "control")
    receipts = LocalHistoryReceiptStore(tmp_path / "control")
    _seed(checkpoints)

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

    first_messages = _Messages(
        {"m1": _message("m1", "t1"), "m2": _message("m2", "t2")},
        fail_message_id="m2",
    )
    with pytest.raises(GmailApiError):
        IncrementalHistorySync(
            _Provider(),
            artifacts,
            checkpoints,
            receipt_store=receipts,
            client_factory=_factory(history, first_messages),
        ).run(run_id=RUN_ID)

    assert len(history.calls) == 2
    committed = checkpoints.load_committed(ACCOUNT_ID)
    assert committed is not None
    assert committed.history_id == "100"

    retry_messages = _Messages({"m2": _message("m2", "t2")})
    result = IncrementalHistorySync(
        _Provider(),
        artifacts,
        checkpoints,
        receipt_store=receipts,
        client_factory=_factory(history, retry_messages),
    ).run(run_id=RUN_ID)

    assert len(history.calls) == 2
    assert result.reused_pages == 2
    assert result.replayed_events == 1
    assert result.messages_hydrated == 1
    assert result.output_history_id == "125"
    assert retry_messages.calls == [("m2", "full")]


def test_cross_run_receipt_skips_already_applied_effect(tmp_path: Path) -> None:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    checkpoints = LocalCheckpointStore(tmp_path / "control")
    receipts = LocalHistoryReceiptStore(tmp_path / "control")
    _seed(checkpoints)

    page = {
        "history": [
            {
                "id": "101",
                "messagesAdded": [{"message": {"id": "m1", "threadId": "t1"}}],
            }
        ],
        "historyId": "110",
    }
    prior_event = map_history_events(
        account_id=ACCOUNT_ID,
        run_id=RUN_ID,
        page=page,
        observed_at=T1,
        source_page_token=None,
    )[0]
    receipts.mark_applied(prior_event, applied_at=T1)

    history = _History({None: page})
    messages = _Messages({})
    result = IncrementalHistorySync(
        _Provider(),
        artifacts,
        checkpoints,
        receipt_store=receipts,
        client_factory=_factory(history, messages),
    ).run(run_id=RUN_B)

    assert result.replayed_events == 1
    assert result.messages_hydrated == 0
    assert messages.calls == []
    event_files = list(
        (artifacts.run_root("gmail.history", RUN_B) / "history_events").glob("*.json")
    )
    assert len(event_files) == 1
    payload = json.loads(event_files[0].read_text(encoding="utf-8"))
    assert payload["processing_status"] == "PROCESSED"


def test_saved_candidate_is_committed_without_recontacting_gmail(tmp_path: Path) -> None:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    checkpoints = LocalCheckpointStore(tmp_path / "control")
    _seed(checkpoints)

    candidate = GmailCheckpoint.candidate(
        account_id=ACCOUNT_ID,
        history_id="125",
        run_id=RUN_ID,
        created_at=T1,
    )
    checkpoints.save_candidate(candidate)
    IncrementalManifestWriter(artifacts).write(
        RUN_ID,
        {
            "schema_version": 1,
            "mode": "incremental_history",
            "run_id": str(RUN_ID),
            "account_id": ACCOUNT_ID,
            "started_at": T1.isoformat(),
            "status": "READY_TO_COMMIT",
            "input_checkpoint": "100",
            "candidate_checkpoint": "125",
            "pages_scanned": 2,
            "events_persisted": 3,
        },
    )

    def forbidden_factory(**kwargs: object) -> _Client:
        raise AssertionError("Gmail must not be contacted during candidate recovery")

    result = IncrementalHistorySync(
        _Provider(),
        artifacts,
        checkpoints,
        receipt_store=LocalHistoryReceiptStore(tmp_path / "control"),
        client_factory=forbidden_factory,
    ).run(run_id=RUN_ID)

    assert result.recovered is True
    assert result.output_history_id == "125"
    assert result.pages_scanned == 2
    assert result.events_persisted == 3
    assert checkpoints.load_candidate(ACCOUNT_ID, RUN_ID) is None


def test_checkpoint_compare_and_swap_rejects_stale_candidate(tmp_path: Path) -> None:
    checkpoints = LocalCheckpointStore(tmp_path / "control")
    _seed(checkpoints)

    stale = GmailCheckpoint.candidate(
        account_id=ACCOUNT_ID,
        history_id="120",
        run_id=RUN_ID,
        created_at=T1,
    )
    winner = GmailCheckpoint.candidate(
        account_id=ACCOUNT_ID,
        history_id="115",
        run_id=RUN_B,
        created_at=T1,
    )
    checkpoints.save_candidate(stale)
    checkpoints.save_candidate(winner)
    checkpoints.commit(winner, expected_committed_history_id="100")

    with pytest.raises(CheckpointConflictError):
        checkpoints.commit(stale, expected_committed_history_id="100")

    committed = checkpoints.load_committed(ACCOUNT_ID)
    assert committed is not None
    assert committed.history_id == "115"


@dataclass
class _SnapshotResult:
    run_id: UUID
    account_id: str = ACCOUNT_ID
    history_start: str | None = "500"
    manifest_status: str = "COMPLETED"


class _SnapshotRunner:
    def __init__(self, result: _SnapshotResult) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    def run(self, account_id: str | None = None, **kwargs: object) -> _SnapshotResult:
        self.calls.append({"account_id": account_id, **kwargs})
        return self.result


def test_expired_cursor_recovers_with_snapshot_bootstrap_then_catch_up(
    tmp_path: Path,
) -> None:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    checkpoints = LocalCheckpointStore(tmp_path / "control")
    receipts = LocalHistoryReceiptStore(tmp_path / "control")
    _seed(checkpoints)

    expired_client = _Client(_History({}, expired=True), _Messages({}))
    catchup_client = _Client(_History({None: {"historyId": "510"}}), _Messages({}))
    factory = _sequence_factory([expired_client, catchup_client])

    snapshot_run_id = UUID("00000000-0000-0000-0000-000000000699")
    snapshot_runner = _SnapshotRunner(
        _SnapshotResult(snapshot_run_id)
    )

    def snapshot_factory(*args: object, **kwargs: object) -> _SnapshotRunner:
        return snapshot_runner

    result = ReliableIncrementalSync(
        _Provider(),
        artifacts,
        checkpoints,
        receipt_store=receipts,
        client_factory=factory,
        snapshot_factory=snapshot_factory,
    ).run(run_id=RUN_ID, snapshot_run_id=snapshot_run_id)

    assert snapshot_runner.calls[0]["run_id"] == snapshot_run_id
    assert result.recovered_with_full_snapshot is True
    assert result.bootstrap_history_id == "500"
    assert result.full_snapshot_run_id == snapshot_runner.result.run_id
    assert result.incremental.input_history_id == "500"
    assert result.incremental.output_history_id == "510"
    committed = checkpoints.load_committed(ACCOUNT_ID)
    assert committed is not None
    assert committed.history_id == "510"



def test_pending_event_with_effect_already_persisted_reuses_effect_and_completes(
    tmp_path: Path,
) -> None:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    checkpoints = LocalCheckpointStore(tmp_path / "control")
    receipts = LocalHistoryReceiptStore(tmp_path / "control")
    _seed(checkpoints)

    page = {
        "history": [
            {
                "id": "101",
                "messagesAdded": [{"message": {"id": "m1", "threadId": "t1"}}],
            }
        ],
        "historyId": "110",
    }
    RawHistoryWriter(artifacts).write_page(
        RUN_ID,
        0,
        page,
        start_history_id="100",
        page_token=None,
    )
    pending = map_history_events(
        account_id=ACCOUNT_ID,
        run_id=RUN_ID,
        page=page,
        observed_at=T1,
        source_page_token=None,
    )[0]
    event_writer = HistoryEventWriter(artifacts)
    event_writer.write(pending)
    RawMessageWriter(artifacts).write(
        RUN_ID,
        "m1",
        _message("m1", "t1"),
    )

    history = _History({})
    messages = _Messages({})
    result = IncrementalHistorySync(
        _Provider(),
        artifacts,
        checkpoints,
        receipt_store=receipts,
        client_factory=_factory(history, messages),
    ).run(run_id=RUN_ID)

    assert history.calls == []
    assert messages.calls == []
    assert result.reused_pages == 1
    assert result.messages_reused == 1
    assert result.messages_hydrated == 0
    assert result.output_history_id == "110"

    processed = event_writer.read(RUN_ID, pending.event_id)
    assert processed is not None
    assert processed.processing_status.value == "PROCESSED"
    assert receipts.load(ACCOUNT_ID, pending.event_id) is not None


def test_committed_same_run_is_recovered_without_recontacting_gmail(
    tmp_path: Path,
) -> None:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    checkpoints = LocalCheckpointStore(tmp_path / "control")
    _seed(checkpoints)

    candidate = GmailCheckpoint.candidate(
        account_id=ACCOUNT_ID,
        history_id="125",
        run_id=RUN_ID,
        created_at=T1,
    )
    checkpoints.save_candidate(candidate)
    committed = checkpoints.commit(
        candidate,
        expected_committed_history_id="100",
    )
    IncrementalManifestWriter(artifacts).write(
        RUN_ID,
        {
            "schema_version": 1,
            "mode": "incremental_history",
            "run_id": str(RUN_ID),
            "account_id": ACCOUNT_ID,
            "started_at": T1.isoformat(),
            "status": "READY_TO_COMMIT",
            "input_checkpoint": "100",
            "candidate_checkpoint": "125",
            "pages_scanned": 2,
            "events_persisted": 4,
        },
    )

    def forbidden_factory(**kwargs: object) -> _Client:
        raise AssertionError("Gmail must not be contacted after the run already committed")

    result = IncrementalHistorySync(
        _Provider(),
        artifacts,
        checkpoints,
        receipt_store=LocalHistoryReceiptStore(tmp_path / "control"),
        client_factory=forbidden_factory,
    ).run(run_id=RUN_ID)

    assert result.recovered is True
    assert result.committed_checkpoint == committed
    assert result.input_history_id == "100"
    assert result.output_history_id == "125"
    manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
    assert manifest["status"] == "RECOVERED_COMMIT"


def test_partial_snapshot_recovery_is_rejected_without_seeding_checkpoint(
    tmp_path: Path,
) -> None:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    checkpoints = LocalCheckpointStore(tmp_path / "control")
    snapshot_runner = _SnapshotRunner(
        _SnapshotResult(
            UUID("00000000-0000-0000-0000-000000000698"),
            manifest_status="PARTIAL",
        )
    )

    def snapshot_factory(*args: object, **kwargs: object) -> _SnapshotRunner:
        return snapshot_runner

    def forbidden_factory(**kwargs: object) -> _Client:
        raise AssertionError("Gmail API client is not needed before snapshot bootstrap")

    with pytest.raises(FullSnapshotRecoveryError, match="did not complete cleanly"):
        ReliableIncrementalSync(
            _Provider(),
            artifacts,
            checkpoints,
            receipt_store=LocalHistoryReceiptStore(tmp_path / "control"),
            client_factory=forbidden_factory,
            snapshot_factory=snapshot_factory,
        ).run(
            run_id=RUN_ID,
            snapshot_run_id=snapshot_runner.result.run_id,
        )

    assert checkpoints.load_committed(ACCOUNT_ID) is None
