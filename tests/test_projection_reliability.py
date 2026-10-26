from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.application import MessageDeltaProjectionService
from gmail_ingestion.application.projection_reliability import ProjectionReliabilityService
from gmail_ingestion.domain import (
    GmailAccount,
    GmailHistoryEvent,
    GmailHistoryEventType,
    HistoryProcessingStatus,
)
from gmail_ingestion.normalization import normalize_snapshot
from gmail_ingestion.persistence import (
    LocalProjectionStore,
    NormalizedParquetWriter,
    RawMessageWriter,
)
from gmail_ingestion.projection import (
    CURRENT_PROJECTION_TABLES,
    ProjectionDeltaBatch,
    ProjectionFoundationService,
    ProjectionGapError,
    ProjectionStatus,
)

ACCOUNT_ID = "acct_0123456789abcdef01234567"
BASELINE_RUN = UUID("00000000-0000-0000-0000-000000000801")
RUN_A = UUID("00000000-0000-0000-0000-000000000802")
RUN_B = UUID("00000000-0000-0000-0000-000000000803")
T0 = datetime(2026, 10, 26, 22, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 26, 22, 1, tzinfo=UTC)
T2 = datetime(2026, 10, 26, 22, 2, tzinfo=UTC)
T3 = datetime(2026, 10, 26, 22, 3, tzinfo=UTC)


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _message(
    message_id: str,
    thread_id: str,
    *,
    history_id: str,
    internal_date: str,
    subject: str,
    label_ids: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "id": message_id,
        "threadId": thread_id,
        "historyId": history_id,
        "internalDate": internal_date,
        "snippet": subject,
        "sizeEstimate": 10,
        "labelIds": list(label_ids),
        "payload": {
            "partId": "",
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Sender <sender@example.com>"},
                {"name": "To", "value": "User <user@example.com>"},
                {"name": "Subject", "value": subject},
            ],
            "body": {"size": len(subject), "data": _b64(subject)},
        },
    }


def _seed_baseline(artifacts: LocalArtifactStore) -> None:
    message = _message(
        "m1",
        "t1",
        history_id="100",
        internal_date="1000",
        subject="baseline",
        label_ids=("INBOX",),
    )
    account = GmailAccount(
        account_id=ACCOUNT_ID,
        email_address="user@example.com",
        messages_total=1,
        threads_total=1,
        history_id="100",
    )
    normalized = normalize_snapshot(
        account,
        [{"id": "INBOX", "name": "INBOX", "type": "system"}],
        [message],
        ingested_at=T0,
        dataset_version="baseline-v1",
        source_connector_version="0.5.0b2",
    )
    NormalizedParquetWriter(artifacts).write(BASELINE_RUN, normalized.tables)


def _event(
    *,
    event_id: str,
    run_id: UUID,
    history_id: str,
    event_type: GmailHistoryEventType,
    message_id: str,
    thread_id: str = "t1",
    label_id: str | None = None,
    observed_at: datetime = T1,
) -> GmailHistoryEvent:
    return GmailHistoryEvent(
        event_id=event_id,
        account_id=ACCOUNT_ID,
        history_id=history_id,
        event_type=event_type,
        message_id=message_id,
        thread_id=thread_id,
        label_id=label_id,
        event_ordinal=0,
        source_page_token=None,
        observed_at=observed_at,
        run_id=run_id,
        processed_at=observed_at,
        processing_status=HistoryProcessingStatus.PROCESSED,
    )


def _setup(tmp_path: Path):
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    _seed_baseline(artifacts)
    projection_store = LocalProjectionStore(tmp_path / "control")
    materializer = MessageDeltaProjectionService(artifacts, projection_store)
    reliability = ProjectionReliabilityService(materializer)
    return artifacts, projection_store, materializer, reliability


def _batch_a(artifacts: LocalArtifactStore) -> ProjectionDeltaBatch:
    added = _message(
        "m2",
        "t1",
        history_id="110",
        internal_date="2000",
        subject="added",
    )
    RawMessageWriter(artifacts).write(RUN_A, "m2", added)
    events = (
        _event(
            event_id="event-add-m2",
            run_id=RUN_A,
            history_id="110",
            event_type=GmailHistoryEventType.MESSAGE_ADDED,
            message_id="m2",
        ),
        _event(
            event_id="event-label-important-m2",
            run_id=RUN_A,
            history_id="115",
            event_type=GmailHistoryEventType.LABEL_ADDED,
            message_id="m2",
            label_id="IMPORTANT",
        ),
    )
    return ProjectionDeltaBatch(
        account_id=ACCOUNT_ID,
        run_id=RUN_A,
        input_history_id="100",
        output_history_id="125",
        events=events,
    )


def _batch_b() -> ProjectionDeltaBatch:
    events = (
        _event(
            event_id="event-delete-m1",
            run_id=RUN_B,
            history_id="130",
            event_type=GmailHistoryEventType.MESSAGE_DELETED,
            message_id="m1",
            observed_at=T2,
        ),
        _event(
            event_id="event-remove-important-m2",
            run_id=RUN_B,
            history_id="140",
            event_type=GmailHistoryEventType.LABEL_REMOVED,
            message_id="m2",
            label_id="IMPORTANT",
            observed_at=T2,
        ),
    )
    return ProjectionDeltaBatch(
        account_id=ACCOUNT_ID,
        run_id=RUN_B,
        input_history_id="125",
        output_history_id="150",
        events=events,
    )


def test_second_generation_incremental_equals_full_rebuild(tmp_path: Path) -> None:
    artifacts, store, materializer, reliability = _setup(tmp_path)
    first_batch = _batch_a(artifacts)

    first_plan = materializer.foundation.plan(
        account_id=ACCOUNT_ID,
        baseline_run_id=BASELINE_RUN,
        baseline_dataset_version="baseline-v1",
        input_checkpoint_history_id="100",
        projected_checkpoint_history_id="125",
        created_at=T0,
    )
    first = reliability.materialize_and_qualify(
        first_plan,
        incremental_batches=(first_batch,),
        rebuild_batches=(first_batch,),
    )
    assert first.convergence.equal is True
    materializer.foundation.promote(
        ACCOUNT_ID,
        first_plan.projection_id,
        promoted_at=T1,
    )

    second_plan = materializer.foundation.plan(
        account_id=ACCOUNT_ID,
        baseline_run_id=BASELINE_RUN,
        baseline_dataset_version="baseline-v1",
        input_checkpoint_history_id="125",
        projected_checkpoint_history_id="150",
        created_at=T2,
    )
    second_batch = _batch_b()
    second = reliability.materialize_and_qualify(
        second_plan,
        incremental_batches=(second_batch,),
        rebuild_batches=(first_batch, second_batch),
    )

    assert second.convergence.equal is True
    assert second.convergence.incremental_digest == second.convergence.rebuild_digest
    assert second.convergence.incremental_batch_count == 1
    assert second.convergence.rebuild_batch_count == 2
    rows = materializer.dataset_store.read_candidate(
        ACCOUNT_ID,
        second_plan.projection_id,
        table_names=CURRENT_PROJECTION_TABLES,
    )
    assert {row["message_id"] for row in rows["messages"]} == {"m2"}
    assert rows["message_labels"] == []
    assert rows["threads"][0]["message_count"] == 1
    assert rows["threads"][0]["first_message_id"] == "m2"
    assert store.load_current(ACCOUNT_ID) is not None


def test_delta_gap_is_rejected_before_candidate_creation(tmp_path: Path) -> None:
    artifacts, store, materializer, reliability = _setup(tmp_path)
    first_batch = _batch_a(artifacts)
    gapped = ProjectionDeltaBatch(
        account_id=ACCOUNT_ID,
        run_id=first_batch.run_id,
        input_history_id="101",
        output_history_id="125",
        events=first_batch.events,
    )
    plan = materializer.foundation.plan(
        account_id=ACCOUNT_ID,
        baseline_run_id=BASELINE_RUN,
        baseline_dataset_version="baseline-v1",
        input_checkpoint_history_id="100",
        projected_checkpoint_history_id="125",
        created_at=T0,
    )

    with pytest.raises(ProjectionGapError, match="gap"):
        reliability.materialize_incremental(plan, (gapped,))

    assert store.load_candidate(ACCOUNT_ID, plan.projection_id) is None


class _SimulatedCrash(RuntimeError):
    pass


class _CrashAfterDataWrite:
    def __init__(self, delegate: ProjectionFoundationService) -> None:
        self.delegate = delegate

    def create_candidate(self, *args: object, **kwargs: object):
        return self.delegate.create_candidate(*args, **kwargs)

    def begin_build(self, *args: object, **kwargs: object):
        return self.delegate.begin_build(*args, **kwargs)

    def validate_candidate(self, *args: object, **kwargs: object):
        raise _SimulatedCrash("crash after candidate data persistence")

    def fail_candidate(self, *args: object, **kwargs: object):
        return self.delegate.fail_candidate(*args, **kwargs)

    def promote(self, *args: object, **kwargs: object):
        return self.delegate.promote(*args, **kwargs)


def test_complete_building_candidate_recovers_without_raw_replay(tmp_path: Path) -> None:
    artifacts, store, materializer, reliability = _setup(tmp_path)
    batch = _batch_a(artifacts)
    plan = materializer.foundation.plan(
        account_id=ACCOUNT_ID,
        baseline_run_id=BASELINE_RUN,
        baseline_dataset_version="baseline-v1",
        input_checkpoint_history_id="100",
        projected_checkpoint_history_id="125",
        created_at=T0,
    )

    real_foundation = materializer.foundation
    materializer.foundation = _CrashAfterDataWrite(real_foundation)  # type: ignore[assignment]
    with pytest.raises(_SimulatedCrash):
        reliability.materialize_incremental(plan, (batch,))

    candidate = store.require_candidate(ACCOUNT_ID, plan.projection_id)
    assert candidate.state.status is ProjectionStatus.BUILDING
    assert store.candidate_data_exists(ACCOUNT_ID, plan.projection_id)

    raw_path = (
        artifacts.run_root("gmail.messages", RUN_A)
        / "raw"
        / "message-m2-full.json"
    )
    raw_path.unlink()

    fresh_materializer = MessageDeltaProjectionService(artifacts, store)
    fresh_reliability = ProjectionReliabilityService(fresh_materializer)
    recovered = fresh_reliability.materialize_incremental(plan, (batch,))

    assert recovered.logical_digest
    assert store.require_candidate(
        ACCOUNT_ID, plan.projection_id
    ).state.status is ProjectionStatus.READY


def test_corrupt_complete_candidate_fails_closed_on_recovery(tmp_path: Path) -> None:
    artifacts, store, materializer, reliability = _setup(tmp_path)
    batch = _batch_a(artifacts)
    plan = materializer.foundation.plan(
        account_id=ACCOUNT_ID,
        baseline_run_id=BASELINE_RUN,
        baseline_dataset_version="baseline-v1",
        input_checkpoint_history_id="100",
        projected_checkpoint_history_id="125",
        created_at=T0,
    )
    real_foundation = materializer.foundation
    materializer.foundation = _CrashAfterDataWrite(real_foundation)  # type: ignore[assignment]
    with pytest.raises(_SimulatedCrash):
        reliability.materialize_incremental(plan, (batch,))

    manifest_path = (
        store.candidate_data_dir(ACCOUNT_ID, plan.projection_id)
        / "projection-data.json"
    )
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["logical_digest"] = "corrupt"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    fresh = ProjectionReliabilityService(
        MessageDeltaProjectionService(artifacts, store)
    )
    with pytest.raises(RuntimeError, match="digest"):
        fresh.materialize_incremental(plan, (batch,))

    assert store.require_candidate(
        ACCOUNT_ID, plan.projection_id
    ).state.status is ProjectionStatus.FAILED
