import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.domain import GmailHistoryEvent, GmailHistoryEventType
from gmail_ingestion.persistence import HistoryEventWriter, RawHistoryWriter


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
    event_path = Path(HistoryEventWriter(store).write(event))

    assert "history-page-000000.json" in raw_uri
    assert event_path.is_file()
    payload = json.loads(event_path.read_text(encoding="utf-8"))
    assert payload["event_type"] == "MESSAGE_ADDED"
    assert payload["message_id"] == "m1"
