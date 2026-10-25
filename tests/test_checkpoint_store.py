import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from gmail_ingestion.domain import CHECKPOINT_SCHEMA_VERSION, CheckpointState, GmailCheckpoint
from gmail_ingestion.persistence import CheckpointConflictError, LocalCheckpointStore

ACCOUNT_A = "acct_0123456789abcdef01234567"
ACCOUNT_B = "acct_89abcdef0123456701234567"
RUN_1 = UUID("00000000-0000-0000-0000-000000000401")
RUN_2 = UUID("00000000-0000-0000-0000-000000000402")
T0 = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 25, 12, 1, tzinfo=UTC)
T2 = datetime(2026, 10, 25, 12, 2, tzinfo=UTC)


def test_candidate_is_persistent_without_advancing_committed(tmp_path: Path) -> None:
    store = LocalCheckpointStore(tmp_path)
    candidate = GmailCheckpoint.candidate(
        account_id=ACCOUNT_A,
        history_id="100",
        run_id=RUN_1,
        created_at=T0,
    )

    store.save_candidate(candidate)

    assert store.load_candidate(ACCOUNT_A, RUN_1) == candidate
    assert store.load_committed(ACCOUNT_A) is None

    payload_path = (
        tmp_path
        / "accounts"
        / ACCOUNT_A
        / "checkpoints"
        / "candidates"
        / f"{RUN_1}.json"
    )
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload["checkpoint_schema_version"] == CHECKPOINT_SCHEMA_VERSION
    assert payload["state"] == "CANDIDATE"


def test_commit_promotes_candidate_and_removes_only_candidate(tmp_path: Path) -> None:
    store = LocalCheckpointStore(tmp_path)
    candidate = GmailCheckpoint.candidate(
        account_id=ACCOUNT_A,
        history_id="125",
        run_id=RUN_1,
        dataset_version="run-401",
        created_at=T0,
    )
    store.save_candidate(candidate)

    committed = store.commit(candidate, committed_at=T1)

    assert committed.state is CheckpointState.COMMITTED
    assert committed.committed_at == T1
    assert store.load_committed(ACCOUNT_A) == committed
    assert store.load_candidate(ACCOUNT_A, RUN_1) is None


def test_new_candidate_does_not_replace_previous_commit_until_commit(tmp_path: Path) -> None:
    store = LocalCheckpointStore(tmp_path)
    first = GmailCheckpoint.candidate(
        account_id=ACCOUNT_A,
        history_id="100",
        run_id=RUN_1,
        created_at=T0,
    )
    store.save_candidate(first)
    committed_100 = store.commit(first, committed_at=T1)

    second = GmailCheckpoint.candidate(
        account_id=ACCOUNT_A,
        history_id="125",
        run_id=RUN_2,
        created_at=T2,
    )
    store.save_candidate(second)

    assert store.load_committed(ACCOUNT_A) == committed_100
    assert store.load_candidate(ACCOUNT_A, RUN_2) == second


def test_discard_candidate_preserves_last_committed_checkpoint(tmp_path: Path) -> None:
    store = LocalCheckpointStore(tmp_path)
    first = GmailCheckpoint.candidate(
        account_id=ACCOUNT_A,
        history_id="100",
        run_id=RUN_1,
        created_at=T0,
    )
    store.save_candidate(first)
    committed = store.commit(first, committed_at=T1)

    second = GmailCheckpoint.candidate(
        account_id=ACCOUNT_A,
        history_id="125",
        run_id=RUN_2,
        created_at=T2,
    )
    store.save_candidate(second)
    store.discard_candidate(ACCOUNT_A, RUN_2)

    assert store.load_committed(ACCOUNT_A) == committed
    assert store.load_candidate(ACCOUNT_A, RUN_2) is None


def test_invalidate_moves_committed_to_invalid_state(tmp_path: Path) -> None:
    store = LocalCheckpointStore(tmp_path)
    candidate = GmailCheckpoint.candidate(
        account_id=ACCOUNT_A,
        history_id="100",
        run_id=RUN_1,
        created_at=T0,
    )
    store.save_candidate(candidate)
    store.commit(candidate, committed_at=T1)

    invalid = store.invalidate(
        ACCOUNT_A,
        "historyId expired",
        invalidated_at=T2,
    )

    assert invalid.state is CheckpointState.INVALID
    assert invalid.invalid_reason == "historyId expired"
    assert invalid.invalidated_at == T2
    assert store.load_committed(ACCOUNT_A) is None
    assert store.load_invalid(ACCOUNT_A) == invalid


def test_checkpoint_namespaces_are_isolated_by_account(tmp_path: Path) -> None:
    store = LocalCheckpointStore(tmp_path)
    a = GmailCheckpoint.candidate(
        account_id=ACCOUNT_A,
        history_id="100",
        run_id=RUN_1,
        created_at=T0,
    )
    b = GmailCheckpoint.candidate(
        account_id=ACCOUNT_B,
        history_id="900",
        run_id=RUN_2,
        created_at=T0,
    )
    store.save_candidate(a)
    store.save_candidate(b)
    committed_a = store.commit(a, committed_at=T1)
    committed_b = store.commit(b, committed_at=T1)

    assert store.load_committed(ACCOUNT_A) == committed_a
    assert store.load_committed(ACCOUNT_B) == committed_b
    assert committed_a.history_id != committed_b.history_id


def test_conflicting_candidate_for_same_run_is_rejected(tmp_path: Path) -> None:
    store = LocalCheckpointStore(tmp_path)
    original = GmailCheckpoint.candidate(
        account_id=ACCOUNT_A,
        history_id="100",
        run_id=RUN_1,
        created_at=T0,
    )
    conflicting = GmailCheckpoint.candidate(
        account_id=ACCOUNT_A,
        history_id="101",
        run_id=RUN_1,
        created_at=T0,
    )
    store.save_candidate(original)

    with pytest.raises(CheckpointConflictError):
        store.save_candidate(conflicting)


def test_checkpoint_requires_timezone_aware_timestamps() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        GmailCheckpoint.candidate(
            account_id=ACCOUNT_A,
            history_id="100",
            run_id=RUN_1,
            created_at=datetime(2026, 10, 25, 12, 0),
        )
