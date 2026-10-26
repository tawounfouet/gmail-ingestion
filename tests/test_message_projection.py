from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.application import (
    MessageDeltaProjectionService,
    MessageProjectionMaterializationError,
)
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
    ProjectionFoundationService,
    ProjectionPlan,
    ProjectionStatus,
)

ACCOUNT_ID = "acct_0123456789abcdef01234567"
BASELINE_RUN = UUID("00000000-0000-0000-0000-000000000701")
DELTA_RUN = UUID("00000000-0000-0000-0000-000000000702")
T0 = datetime(2026, 10, 26, 21, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 26, 21, 1, tzinfo=UTC)
T2 = datetime(2026, 10, 26, 21, 2, tzinfo=UTC)


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


def _seed_baseline(
    artifact_store: LocalArtifactStore,
    messages: list[dict[str, object]],
) -> None:
    account = GmailAccount(
        account_id=ACCOUNT_ID,
        email_address="user@example.com",
        messages_total=len(messages),
        threads_total=len({str(message["threadId"]) for message in messages}),
        history_id="100",
    )
    label_ids = sorted(
        {
            str(label_id)
            for message in messages
            for label_id in message.get("labelIds", [])
        }
    )
    labels = [
        {"id": label_id, "name": label_id, "type": "system"}
        for label_id in label_ids
    ]
    normalized = normalize_snapshot(
        account,
        labels,
        messages,
        ingested_at=T0,
        dataset_version="baseline-v1",
        source_connector_version="0.5.0a1",
    )
    NormalizedParquetWriter(artifact_store).write(BASELINE_RUN, normalized.tables)


def _event(
    event_id: str,
    event_type: GmailHistoryEventType,
    message_id: str,
    history_id: str,
    *,
    thread_id: str = "t1",
    processed: bool = True,
) -> GmailHistoryEvent:
    return GmailHistoryEvent(
        event_id=event_id,
        account_id=ACCOUNT_ID,
        history_id=history_id,
        event_type=event_type,
        message_id=message_id,
        thread_id=thread_id,
        label_id=None,
        event_ordinal=0,
        source_page_token=None,
        observed_at=T1,
        run_id=DELTA_RUN,
        processed_at=T2 if processed else None,
        processing_status=(
            HistoryProcessingStatus.PROCESSED
            if processed
            else HistoryProcessingStatus.PENDING
        ),
    )


def _service(
    tmp_path: Path,
    *,
    messages: list[dict[str, object]],
) -> tuple[
    LocalArtifactStore,
    LocalProjectionStore,
    MessageDeltaProjectionService,
    ProjectionPlan,
]:
    artifact_store = LocalArtifactStore(tmp_path / "artifacts")
    _seed_baseline(artifact_store, messages)
    projection_store = LocalProjectionStore(tmp_path / "control")
    foundation = ProjectionFoundationService(projection_store)
    plan = foundation.plan(
        account_id=ACCOUNT_ID,
        baseline_run_id=BASELINE_RUN,
        baseline_dataset_version="baseline-v1",
        input_checkpoint_history_id="100",
        projected_checkpoint_history_id="125",
        created_at=T0,
    )
    return (
        artifact_store,
        projection_store,
        MessageDeltaProjectionService(artifact_store, projection_store),
        plan,
    )


def test_message_added_upserts_current_message_and_dependent_rows(tmp_path: Path) -> None:
    baseline = [_message("m1", "t1", history_id="100", internal_date="1000", subject="one")]
    artifacts, store, service, plan = _service(tmp_path, messages=baseline)
    added = _message("m2", "t2", history_id="110", internal_date="2000", subject="two")
    RawMessageWriter(artifacts).write(DELTA_RUN, "m2", added)
    event = _event("event-add-m2", GmailHistoryEventType.MESSAGE_ADDED, "m2", "110", thread_id="t2")

    result = service.materialize(plan, [event])
    candidate = store.require_candidate(ACCOUNT_ID, plan.projection_id)
    rows = service.dataset_store.read_candidate(ACCOUNT_ID, plan.projection_id)

    assert candidate.state.status is ProjectionStatus.READY
    assert candidate.state.logical_digest == result.logical_digest
    assert {row["message_id"] for row in rows["messages"]} == {"m1", "m2"}
    assert any(row["message_id"] == "m2" for row in rows["message_headers"])
    assert any(row["message_id"] == "m2" for row in rows["message_recipients"])
    assert result.messages_added == 1
    assert result.messages_deleted == 0


def test_message_deleted_physically_excludes_message_and_children(tmp_path: Path) -> None:
    baseline = [
        _message("m1", "t1", history_id="100", internal_date="1000", subject="one"),
        _message("m2", "t1", history_id="100", internal_date="2000", subject="two"),
    ]
    _, _, service, plan = _service(tmp_path, messages=baseline)
    event = _event("event-delete-m1", GmailHistoryEventType.MESSAGE_DELETED, "m1", "120")

    result = service.materialize(plan, [event])
    rows = service.dataset_store.read_candidate(ACCOUNT_ID, plan.projection_id)

    assert {row["message_id"] for row in rows["messages"]} == {"m2"}
    for name, table_rows in rows.items():
        assert all(row.get("message_id") != "m1" for row in table_rows), name
    assert result.messages_deleted == 1


def test_duplicate_message_event_replay_is_idempotent(tmp_path: Path) -> None:
    baseline = [_message("m1", "t1", history_id="100", internal_date="1000", subject="one")]
    artifacts, _, service, plan = _service(tmp_path, messages=baseline)
    added = _message("m2", "t2", history_id="110", internal_date="2000", subject="two")
    RawMessageWriter(artifacts).write(DELTA_RUN, "m2", added)
    event = _event("event-add-m2", GmailHistoryEventType.MESSAGE_ADDED, "m2", "110", thread_id="t2")

    first = service.materialize(plan, [event, event])
    second = service.materialize(plan, [event, event])

    assert first.logical_digest == second.logical_digest
    assert first.duplicate_events_ignored == 1
    assert second.duplicate_events_ignored == 1
    assert first.row_counts == second.row_counts


def test_history_order_wins_over_input_order(tmp_path: Path) -> None:
    baseline = [_message("m1", "t1", history_id="100", internal_date="1000", subject="one")]
    artifacts, _, service, plan = _service(tmp_path, messages=baseline)
    added = _message("m2", "t2", history_id="110", internal_date="2000", subject="two")
    RawMessageWriter(artifacts).write(DELTA_RUN, "m2", added)
    add_event = _event("event-add-m2", GmailHistoryEventType.MESSAGE_ADDED, "m2", "110", thread_id="t2")
    delete_event = _event(
        "event-delete-m2",
        GmailHistoryEventType.MESSAGE_DELETED,
        "m2",
        "120",
        thread_id="t2",
    )

    service.materialize(plan, [delete_event, add_event])
    rows = service.dataset_store.read_candidate(ACCOUNT_ID, plan.projection_id)

    assert {row["message_id"] for row in rows["messages"]} == {"m1"}


def test_promotion_moves_projection_data_before_current_pointer(tmp_path: Path) -> None:
    baseline = [_message("m1", "t1", history_id="100", internal_date="1000", subject="one")]
    _, store, service, plan = _service(tmp_path, messages=baseline)
    event = _event("event-delete-m1", GmailHistoryEventType.MESSAGE_DELETED, "m1", "120")

    service.materialize(plan, [event])
    pointer = service.foundation.promote(ACCOUNT_ID, plan.projection_id, promoted_at=T2)
    version_rows = service.dataset_store.read_version(ACCOUNT_ID, plan.projection_id)

    assert pointer.projection_id == plan.projection_id
    assert version_rows["messages"] == []
    assert not (
        tmp_path
        / "control"
        / "accounts"
        / ACCOUNT_ID
        / "projections"
        / "candidate_data"
        / plan.projection_id
    ).exists()
    assert (
        tmp_path
        / "control"
        / "accounts"
        / ACCOUNT_ID
        / "projections"
        / "version_data"
        / plan.projection_id
    ).is_dir()


def test_missing_persisted_raw_fails_candidate_without_gmail_call(tmp_path: Path) -> None:
    baseline = [_message("m1", "t1", history_id="100", internal_date="1000", subject="one")]
    _, store, service, plan = _service(tmp_path, messages=baseline)
    event = _event("event-add-missing", GmailHistoryEventType.MESSAGE_ADDED, "missing", "110")

    with pytest.raises(MessageProjectionMaterializationError, match="Persisted RAW"):
        service.materialize(plan, [event])

    candidate = store.require_candidate(ACCOUNT_ID, plan.projection_id)
    assert candidate.state.status is ProjectionStatus.FAILED
    assert store.load_current(ACCOUNT_ID) is None


def test_unprocessed_event_is_rejected_fail_closed(tmp_path: Path) -> None:
    baseline = [_message("m1", "t1", history_id="100", internal_date="1000", subject="one")]
    _, store, service, plan = _service(tmp_path, messages=baseline)
    event = _event(
        "event-pending",
        GmailHistoryEventType.MESSAGE_DELETED,
        "m1",
        "110",
        processed=False,
    )

    with pytest.raises(MessageProjectionMaterializationError, match="PROCESSED"):
        service.materialize(plan, [event])

    assert store.require_candidate(ACCOUNT_ID, plan.projection_id).state.status is ProjectionStatus.FAILED


def test_label_added_upserts_current_relation(tmp_path: Path) -> None:
    baseline = [_message("m1", "t1", history_id="100", internal_date="1000", subject="one")]
    _, _, service, plan = _service(tmp_path, messages=baseline)
    event = GmailHistoryEvent(
        event_id="event-label-add",
        account_id=ACCOUNT_ID,
        history_id="110",
        event_type=GmailHistoryEventType.LABEL_ADDED,
        message_id="m1",
        thread_id="t1",
        label_id="IMPORTANT",
        event_ordinal=0,
        source_page_token=None,
        observed_at=T1,
        run_id=DELTA_RUN,
        processed_at=T2,
        processing_status=HistoryProcessingStatus.PROCESSED,
    )

    result = service.materialize(plan, [event])
    rows = service.dataset_store.read_candidate(
        ACCOUNT_ID,
        plan.projection_id,
        table_names=CURRENT_PROJECTION_TABLES,
    )

    assert result.labels_added == 1
    assert rows["message_labels"] == [
        {
            "account_id": ACCOUNT_ID,
            "message_id": "m1",
            "label_id": "IMPORTANT",
            "added_history_id": "110",
            "removed_history_id": None,
            "is_active": True,
            "first_seen_at": T1,
            "last_seen_at": T1,
        }
    ]


def test_label_removed_physically_excludes_current_relation(tmp_path: Path) -> None:
    baseline = [
        _message(
            "m1",
            "t1",
            history_id="100",
            internal_date="1000",
            subject="one",
            label_ids=("INBOX",),
        )
    ]
    _, _, service, plan = _service(tmp_path, messages=baseline)
    event = GmailHistoryEvent(
        event_id="event-label-remove",
        account_id=ACCOUNT_ID,
        history_id="110",
        event_type=GmailHistoryEventType.LABEL_REMOVED,
        message_id="m1",
        thread_id="t1",
        label_id="INBOX",
        event_ordinal=0,
        source_page_token=None,
        observed_at=T1,
        run_id=DELTA_RUN,
        processed_at=T2,
        processing_status=HistoryProcessingStatus.PROCESSED,
    )

    result = service.materialize(plan, [event])
    rows = service.dataset_store.read_candidate(
        ACCOUNT_ID,
        plan.projection_id,
        table_names=CURRENT_PROJECTION_TABLES,
    )

    assert result.labels_removed == 1
    assert rows["message_labels"] == []


def test_thread_projection_recomputes_after_message_addition(tmp_path: Path) -> None:
    baseline = [_message("m1", "t1", history_id="100", internal_date="1000", subject="one")]
    artifacts, _, service, plan = _service(tmp_path, messages=baseline)
    added = _message("m2", "t1", history_id="110", internal_date="2000", subject="two")
    RawMessageWriter(artifacts).write(DELTA_RUN, "m2", added)
    event = _event("event-add-m2", GmailHistoryEventType.MESSAGE_ADDED, "m2", "110")

    service.materialize(plan, [event])
    rows = service.dataset_store.read_candidate(
        ACCOUNT_ID,
        plan.projection_id,
        table_names=CURRENT_PROJECTION_TABLES,
    )

    assert len(rows["threads"]) == 1
    thread = rows["threads"][0]
    assert thread["thread_id"] == "t1"
    assert thread["message_count"] == 2
    assert thread["first_message_id"] == "m1"
    assert thread["last_message_id"] == "m2"
    assert thread["history_id"] == "110"


def test_thread_projection_disappears_when_last_message_is_deleted(tmp_path: Path) -> None:
    baseline = [_message("m1", "t1", history_id="100", internal_date="1000", subject="one")]
    _, _, service, plan = _service(tmp_path, messages=baseline)
    event = _event("event-delete-m1", GmailHistoryEventType.MESSAGE_DELETED, "m1", "120")

    service.materialize(plan, [event])
    rows = service.dataset_store.read_candidate(
        ACCOUNT_ID,
        plan.projection_id,
        table_names=CURRENT_PROJECTION_TABLES,
    )

    assert rows["messages"] == []
    assert rows["threads"] == []


def test_all_four_incremental_event_classes_converge_in_current_projection(
    tmp_path: Path,
) -> None:
    baseline = [
        _message(
            "m1",
            "t1",
            history_id="100",
            internal_date="1000",
            subject="one",
            label_ids=("INBOX",),
        )
    ]
    artifacts, _, service, plan = _service(tmp_path, messages=baseline)
    added = _message("m2", "t1", history_id="110", internal_date="2000", subject="two")
    RawMessageWriter(artifacts).write(DELTA_RUN, "m2", added)

    events = [
        GmailHistoryEvent(
            event_id="event-label-add-m2",
            account_id=ACCOUNT_ID,
            history_id="111",
            event_type=GmailHistoryEventType.LABEL_ADDED,
            message_id="m2",
            thread_id="t1",
            label_id="IMPORTANT",
            event_ordinal=0,
            source_page_token=None,
            observed_at=T1,
            run_id=DELTA_RUN,
            processed_at=T2,
            processing_status=HistoryProcessingStatus.PROCESSED,
        ),
        _event("event-delete-m1", GmailHistoryEventType.MESSAGE_DELETED, "m1", "120"),
        _event("event-add-m2", GmailHistoryEventType.MESSAGE_ADDED, "m2", "110"),
        GmailHistoryEvent(
            event_id="event-label-remove-m1",
            account_id=ACCOUNT_ID,
            history_id="112",
            event_type=GmailHistoryEventType.LABEL_REMOVED,
            message_id="m1",
            thread_id="t1",
            label_id="INBOX",
            event_ordinal=0,
            source_page_token=None,
            observed_at=T1,
            run_id=DELTA_RUN,
            processed_at=T2,
            processing_status=HistoryProcessingStatus.PROCESSED,
        ),
    ]

    result = service.materialize(plan, events)
    rows = service.dataset_store.read_candidate(
        ACCOUNT_ID,
        plan.projection_id,
        table_names=CURRENT_PROJECTION_TABLES,
    )

    assert {row["message_id"] for row in rows["messages"]} == {"m2"}
    assert {(row["message_id"], row["label_id"]) for row in rows["message_labels"]} == {
        ("m2", "IMPORTANT")
    }
    assert rows["threads"][0]["message_count"] == 1
    assert rows["threads"][0]["first_message_id"] == "m2"
    assert result.messages_added == 1
    assert result.messages_deleted == 1
    assert result.labels_added == 1
    assert result.labels_removed == 1


def test_unavailable_added_message_is_safe_when_later_deleted(tmp_path: Path) -> None:
    baseline = [_message("m1", "t1", history_id="100", internal_date="1000", subject="one")]
    _, store, service, plan = _service(tmp_path, messages=baseline)
    add_event = _event(
        "event-add-unavailable",
        GmailHistoryEventType.MESSAGE_ADDED,
        "m2",
        "110",
        thread_id="t2",
    )
    delete_event = _event(
        "event-delete-unavailable",
        GmailHistoryEventType.MESSAGE_DELETED,
        "m2",
        "120",
        thread_id="t2",
    )

    result = service.materialize(plan, [delete_event, add_event])
    rows = service.dataset_store.read_candidate(
        ACCOUNT_ID,
        plan.projection_id,
        table_names=CURRENT_PROJECTION_TABLES,
    )

    assert result.messages_added == 1
    assert result.messages_deleted == 1
    assert {row["message_id"] for row in rows["messages"]} == {"m1"}
    assert {row["thread_id"] for row in rows["threads"]} == {"t1"}
    assert store.require_candidate(
        ACCOUNT_ID,
        plan.projection_id,
    ).state.status is ProjectionStatus.READY
