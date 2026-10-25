from datetime import UTC, datetime
from uuid import UUID

from gmail_ingestion.domain import GmailHistoryEventType, map_message_added_events


def test_message_added_mapping_is_elementary_stable_and_ignores_generic_messages() -> None:
    run_id = UUID("00000000-0000-0000-0000-000000000411")
    observed = datetime(2026, 10, 25, 13, 0, tzinfo=UTC)
    page = {
        "history": [
            {
                "id": "101",
                "messages": [{"id": "generic", "threadId": "tg"}],
                "messagesAdded": [
                    {"message": {"id": "m1", "threadId": "t1"}},
                    {"message": {"id": "m2", "threadId": "t2"}},
                ],
            },
            {
                "id": "105",
                "messagesAdded": [{"message": {"id": "m3", "threadId": "t3"}}],
            },
        ],
        "historyId": "110",
    }

    first = map_message_added_events(
        account_id="acct_0123456789abcdef01234567",
        run_id=run_id,
        page=page,
        observed_at=observed,
        source_page_token=None,
    )
    second = map_message_added_events(
        account_id="acct_0123456789abcdef01234567",
        run_id=run_id,
        page=page,
        observed_at=observed,
        source_page_token=None,
    )

    assert [event.message_id for event in first] == ["m1", "m2", "m3"]
    assert [event.history_id for event in first] == ["101", "101", "105"]
    assert all(event.event_type is GmailHistoryEventType.MESSAGE_ADDED for event in first)
    assert [event.event_ordinal for event in first] == [0, 1, 0]
    assert [event.event_id for event in first] == [event.event_id for event in second]
    assert "generic" not in {event.message_id for event in first}
