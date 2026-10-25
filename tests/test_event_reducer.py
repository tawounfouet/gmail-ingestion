from datetime import UTC, datetime
from uuid import UUID

from gmail_ingestion.domain import GmailHistoryEvent, GmailHistoryEventType
from gmail_ingestion.sync import LabelMutationOperation, reduce_history_event

ACCOUNT_ID = "acct_0123456789abcdef01234567"
RUN_ID = UUID("00000000-0000-0000-0000-000000000415")
OBSERVED = datetime(2026, 10, 25, 14, 0, tzinfo=UTC)


def _event(
    event_type: GmailHistoryEventType,
    *,
    message_id: str = "m1",
    label_id: str | None = None,
) -> GmailHistoryEvent:
    return GmailHistoryEvent(
        event_id=f"event-{event_type.value}-{label_id or 'none'}",
        account_id=ACCOUNT_ID,
        history_id="501",
        event_type=event_type,
        message_id=message_id,
        thread_id="t1",
        label_id=label_id,
        event_ordinal=0,
        source_page_token=None,
        observed_at=OBSERVED,
        run_id=RUN_ID,
    )


def test_message_added_reducer_requests_full_hydration() -> None:
    reduced = reduce_history_event(_event(GmailHistoryEventType.MESSAGE_ADDED))

    assert reduced.hydrate_message_id == "m1"
    assert reduced.tombstone is None
    assert reduced.label_mutation is None


def test_message_deleted_reducer_emits_tombstone() -> None:
    event = _event(GmailHistoryEventType.MESSAGE_DELETED, message_id="m2")
    reduced = reduce_history_event(event)

    assert reduced.hydrate_message_id is None
    assert reduced.label_mutation is None
    assert reduced.tombstone is not None
    assert reduced.tombstone.message_id == "m2"
    assert reduced.tombstone.deletion_history_id == "501"
    assert reduced.tombstone.source_event_id == event.event_id


def test_label_add_and_remove_reducers_emit_relation_mutations() -> None:
    added = reduce_history_event(
        _event(GmailHistoryEventType.LABEL_ADDED, label_id="Label_1")
    ).label_mutation
    removed = reduce_history_event(
        _event(GmailHistoryEventType.LABEL_REMOVED, label_id="Label_1")
    ).label_mutation

    assert added is not None
    assert added.operation is LabelMutationOperation.UPSERT_ACTIVE
    assert added.is_active is True
    assert removed is not None
    assert removed.operation is LabelMutationOperation.DEACTIVATE
    assert removed.is_active is False
