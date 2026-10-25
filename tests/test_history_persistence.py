import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.domain import (
    GmailHistoryEvent,
    GmailHistoryEventType,
    HistoryProcessingStatus,
)
from gmail_ingestion.persistence import (
    HistoryEventWriter,
    IncrementalMutationWriter,
    RawHistoryWriter,
)
from gmail_ingestion.sync import reduce_history_event


def test_history_page_and_event_are_persisted_separately(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    run_id = UUID("00000000-0000-0000-0000-000000000412")
    page = {
        "history": [
            {
                "id": "101",
                "messagesAdded": [{"message": {"id": "m1", "threadId": "t1"}}],
            }
        ],
        "historyId": "110",
    }

    raw_uri = RawHistoryWriter(store).write_page(
        run_id,
        0,
        page,
        start_history_id="100",
        page_token=None,
    )
    event = GmailHistoryEvent(
        event_id="event-1",
        account_id="acct_0123456789abcdef01234567",
        history_id="101",
        event_type=GmailHistoryEventType.MESSAGE_ADDED,
        message_id="m1",
        thread_id="t1",
        label_id=None,
        event_ordinal=0,
        source_page_token=None,
        observed_at=datetime(2026, 10, 25, 13, 0, tzinfo=UTC),
        run_id=run_id,
    )
    event_writer = HistoryEventWriter(store)
    event_path = Path(event_writer.write(event))
    event_writer.write(
        event.mark_processed(
            processed_at=datetime(2026, 10, 25, 13, 1, tzinfo=UTC)
        )
    )

    assert "history-page-000000.json" in raw_uri
    assert event_path.is_file()
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    assert payload["event_type"] == "MESSAGE_ADDED"
    assert payload["message_id"] == "m1"
    assert payload["processing_status"] == HistoryProcessingStatus.PROCESSED.value
    assert payload["processed_at"] is not None


def test_mutation_writer_persists_tombstone_and_label_delta(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    run_id = UUID("00000000-0000-0000-0000-000000000416")
    writer = IncrementalMutationWriter(store)

    deleted_event = GmailHistoryEvent(
        event_id="delete-event",
        account_id="acct_0123456789abcdef01234567",
        history_id="510",
        event_type=GmailHistoryEventType.MESSAGE_DELETED,
        message_id="m2",
        thread_id="t2",
        label_id=None,
        event_ordinal=0,
        source_page_token=None,
        observed_at=datetime(2026, 10, 25, 14, 0, tzinfo=UTC),
        run_id=run_id,
    )
    label_event = GmailHistoryEvent(
        event_id="label-event",
        account_id="acct_0123456789abcdef01234567",
        history_id="511",
        event_type=GmailHistoryEventType.LABEL_REMOVED,
        message_id="m1",
        thread_id="t1",
        label_id="INBOX",
        event_ordinal=0,
        source_page_token=None,
        observed_at=datetime(2026, 10, 25, 14, 1, tzinfo=UTC),
        run_id=run_id,
    )

    tombstone = reduce_history_event(deleted_event).tombstone
    mutation = reduce_history_event(label_event).label_mutation
    assert tombstone is not None
    assert mutation is not None

    tombstone_path = Path(writer.write_tombstone(tombstone))
    mutation_path = Path(writer.write_label_mutation(mutation))

    tombstone_payload = json.loads(tombstone_path.read_text(encoding="utf-8"))
    mutation_payload = json.loads(mutation_path.read_text(encoding="utf-8"))
    assert tombstone_payload["deletion_history_id"] == "510"
    assert tombstone_payload["source_event_id"] == "delete-event"
    assert mutation_payload["operation"] == "DEACTIVATE"
    assert mutation_payload["is_active"] is False
