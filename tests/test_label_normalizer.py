from datetime import UTC, datetime

import pytest

from gmail_ingestion.normalization import GmailLabelNormalizationError, normalize_labels


def test_normalize_labels_matches_target_schema() -> None:
    timestamp = datetime(2026, 10, 24, 7, 30, tzinfo=UTC)
    dataset = normalize_labels(
        "acct_0123456789abcdef01234567",
        [
            {
                "id": "INBOX",
                "name": "INBOX",
                "type": "system",
                "messageListVisibility": "show",
                "labelListVisibility": "labelShow",
                "messagesTotal": 12,
                "messagesUnread": 2,
                "threadsTotal": 7,
                "threadsUnread": 1,
            },
            {
                "id": "Label_1",
                "name": "Clients",
                "type": "user",
                "color": {"textColor": "#ffffff", "backgroundColor": "#4986e7"},
            },
        ],
        ingested_at=timestamp,
    )

    assert dataset.row_count == 2
    system, user = dataset.to_rows()
    assert system["is_system"] is True
    assert system["messages_total"] == 12
    assert user["is_system"] is False
    assert user["color_text"] == "#ffffff"
    assert user["color_background"] == "#4986e7"
    assert user["ingested_at"] == timestamp


def test_normalize_label_requires_id_and_name() -> None:
    with pytest.raises(GmailLabelNormalizationError, match="id"):
        normalize_labels("acct_0123456789abcdef01234567", [{"name": "missing-id"}])
