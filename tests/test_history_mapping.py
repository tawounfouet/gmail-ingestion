from datetime import UTC, datetime
from uuid import UUID

from gmail_ingestion.domain import (
    GmailHistoryEventType,
    HistoryProcessingStatus,
    map_history_events,
    map_message_added_events,
)


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


def test_history_mapping_expands_all_specific_change_types() -> None:
    run_id = UUID("00000000-0000-0000-0000-000000000413")
    observed = datetime(2026, 10, 25, 13, 5, tzinfo=UTC)
    page = {
        "history": [
            {
                "id": "201",
                "messages": [{"id": "generic", "threadId": "tg"}],
                "messagesAdded": [{"message": {"id": "m1", "threadId": "t1"}}],
                "messagesDeleted": [{"message": {"id": "m2", "threadId": "t2"}}],
                "labelsAdded": [
                    {
                        "message": {"id": "m1", "threadId": "t1"},
                        "labelIds": ["INBOX", "Label_1"],
                    }
                ],
                "labelsRemoved": [
                    {
                        "message": {"id": "m3", "threadId": "t3"},
                        "labelIds": ["STARRED"],
                    }
                ],
            }
        ],
        "historyId": "205",
    }

    events = map_history_events(
        account_id="acct_0123456789abcdef01234567",
        run_id=run_id,
        page=page,
        observed_at=observed,
        source_page_token="p1",
    )

    assert [
        (event.event_type, event.message_id, event.label_id)
        for event in events
    ] == [
        (GmailHistoryEventType.MESSAGE_ADDED, "m1", None),
        (GmailHistoryEventType.MESSAGE_DELETED, "m2", None),
        (GmailHistoryEventType.LABEL_ADDED, "m1", "INBOX"),
        (GmailHistoryEventType.LABEL_ADDED, "m1", "Label_1"),
        (GmailHistoryEventType.LABEL_REMOVED, "m3", "STARRED"),
    ]
    assert [event.event_ordinal for event in events] == [0, 0, 0, 1, 0]
    assert all(event.processing_status is HistoryProcessingStatus.PENDING for event in events)
    assert all(event.processed_at is None for event in events)
    assert "generic" not in {event.message_id for event in events}


def test_history_event_mark_processed_preserves_identity() -> None:
    event = map_history_events(
        account_id="acct_0123456789abcdef01234567",
        run_id=UUID("00000000-0000-0000-0000-000000000414"),
        page={
            "history": [
                {
                    "id": "300",
                    "messagesDeleted": [{"message": {"id": "m9", "threadId": "t9"}}],
                }
            ]
        },
        observed_at=datetime(2026, 10, 25, 13, 10, tzinfo=UTC),
        source_page_token=None,
    )[0]

    processed = event.mark_processed(
        processed_at=datetime(2026, 10, 25, 13, 11, tzinfo=UTC)
    )

    assert processed.event_id == event.event_id
    assert processed.processing_status is HistoryProcessingStatus.PROCESSED
    assert processed.processed_at == datetime(2026, 10, 25, 13, 11, tzinfo=UTC)
