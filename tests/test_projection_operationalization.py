from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.application import ProjectCurrentState
from gmail_ingestion.domain import (
    GmailAccount,
    GmailHistoryEvent,
    GmailHistoryEventType,
    HistoryProcessingStatus,
)
from gmail_ingestion.normalization import normalize_snapshot
from gmail_ingestion.persistence import (
    HistoryEventWriter,
    IncrementalManifestWriter,
    LocalProjectionBatchReader,
    LocalProjectionStore,
    NormalizedParquetWriter,
    ProjectionBatchReadError,
    RawMessageWriter,
)

ACCOUNT_ID = "acct_0123456789abcdef01234567"
BASELINE_RUN = UUID("00000000-0000-0000-0000-000000000901")
DELTA_RUN = UUID("00000000-0000-0000-0000-000000000902")
T0 = datetime(2026, 10, 26, 23, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 26, 23, 1, tzinfo=UTC)


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _message(message_id: str, history_id: str, subject: str) -> dict[str, object]:
    return {
        "id": message_id,
        "threadId": "t1",
        "historyId": history_id,
        "internalDate": "1000" if message_id == "m1" else "2000",
        "snippet": subject,
        "sizeEstimate": 10,
        "labelIds": [],
        "payload": {
            "partId": "",
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "sender@example.com"},
                {"name": "To", "value": "user@example.com"},
                {"name": "Subject", "value": subject},
            ],
            "body": {"size": len(subject), "data": _b64(subject)},
        },
    }


def _seed(artifacts: LocalArtifactStore, *, status: str = "COMPLETED") -> GmailHistoryEvent:
    account = GmailAccount(
        account_id=ACCOUNT_ID,
        email_address="user@example.com",
        messages_total=1,
        threads_total=1,
        history_id="100",
    )
    normalized = normalize_snapshot(
        account,
        (),
        (_message("m1", "100", "baseline"),),
        ingested_at=T0,
        dataset_version=str(BASELINE_RUN),
        source_connector_version="0.5.0rc1",
    )
    NormalizedParquetWriter(artifacts).write(BASELINE_RUN, normalized.tables)

    RawMessageWriter(artifacts).write(
        DELTA_RUN,
        "m2",
        _message("m2", "110", "added"),
    )
    event = GmailHistoryEvent(
        event_id="event-add-m2",
        account_id=ACCOUNT_ID,
        history_id="110",
        event_type=GmailHistoryEventType.MESSAGE_ADDED,
        message_id="m2",
        thread_id="t1",
        label_id=None,
        event_ordinal=0,
        source_page_token=None,
        observed_at=T1,
        run_id=DELTA_RUN,
        processed_at=T1,
        processing_status=HistoryProcessingStatus.PROCESSED,
    )
    HistoryEventWriter(artifacts).write(event)
    IncrementalManifestWriter(artifacts).write(
        DELTA_RUN,
        {
            "schema_version": 1,
            "mode": "incremental_history",
            "run_id": str(DELTA_RUN),
            "account_id": ACCOUNT_ID,
            "status": status,
            "input_checkpoint": "100",
            "candidate_checkpoint": "125",
            "committed_checkpoint": "125" if status == "COMPLETED" else None,
            "events_persisted": 1,
        },
    )
    return event


def test_local_batch_reader_builds_batch_from_committed_incremental_evidence(
    tmp_path: Path,
) -> None:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    event = _seed(artifacts)

    batch = LocalProjectionBatchReader(artifacts).read(DELTA_RUN)

    assert batch.account_id == ACCOUNT_ID
    assert batch.input_history_id == "100"
    assert batch.output_history_id == "125"
    assert batch.events == (event,)


def test_local_batch_reader_rejects_uncommitted_run(tmp_path: Path) -> None:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    _seed(artifacts, status="FAILED")

    with pytest.raises(ProjectionBatchReadError, match="not committed"):
        LocalProjectionBatchReader(artifacts).read(DELTA_RUN)


def test_project_current_state_operates_directly_from_persisted_run_ids(tmp_path: Path) -> None:
    artifacts = LocalArtifactStore(tmp_path / "artifacts")
    _seed(artifacts)
    projections = LocalProjectionStore(tmp_path / "control")
    use_case = ProjectCurrentState(artifacts, projections)

    result = use_case.run(
        account_id=ACCOUNT_ID,
        baseline_run_id=BASELINE_RUN,
        baseline_dataset_version=str(BASELINE_RUN),
        delta_run_ids=(DELTA_RUN,),
    )

    assert result.promoted is True
    assert result.already_current is False
    assert result.target_history_id == "125"
    assert result.logical_digest == result.rebuild_digest
    current = projections.load_current(ACCOUNT_ID)
    assert current is not None
    assert current.projection_id == result.projection_id

    repeated = use_case.run(
        account_id=ACCOUNT_ID,
        baseline_run_id=BASELINE_RUN,
        baseline_dataset_version=str(BASELINE_RUN),
        delta_run_ids=(DELTA_RUN,),
    )
    assert repeated.already_current is True
    assert repeated.projection_id == result.projection_id
