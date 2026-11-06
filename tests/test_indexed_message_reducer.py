"""Equivalence and bounded-work tests for the indexed Current State reducer."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest

from gmail_ingestion.domain import (
    GmailHistoryEvent,
    GmailHistoryEventType,
    HistoryProcessingStatus,
)
from gmail_ingestion.projection.indexed_message_reducer import (
    IndexedMessageProjectionReducer,
)
from gmail_ingestion.projection.message_delta import (
    MESSAGE_PROJECTION_TABLES,
    PreparedMessageDelta,
)
from gmail_ingestion.projection.message_reducer import (
    MessageProjectionReducer,
    MessageProjectionReductionError,
)

ACCOUNT = "acct_0123456789abcdef01234567"
RUN = UUID("00000000-0000-0000-0000-000000000777")
NOW = datetime(2026, 11, 6, tzinfo=UTC)


def _event(
    index: int,
    event_type: GmailHistoryEventType,
    message_id: str,
    label_id: str | None = None,
) -> GmailHistoryEvent:
    return GmailHistoryEvent(
        event_id=f"ev-{index:05d}",
        account_id=ACCOUNT,
        history_id=str(200 + index),
        event_type=event_type,
        message_id=message_id,
        thread_id=f"thread-{message_id}",
        label_id=label_id,
        event_ordinal=0,
        source_page_token=None,
        observed_at=NOW,
        run_id=RUN,
        processed_at=NOW,
        processing_status=HistoryProcessingStatus.PROCESSED,
    )


def _normalized_message(message_id: str) -> dict[str, list[dict[str, Any]]]:
    return {
        "messages": [
            {"account_id": ACCOUNT, "message_id": message_id, "thread_id": f"thread-{message_id}"}
        ],
        "message_headers": [
            {
                "account_id": ACCOUNT,
                "message_id": message_id,
                "part_id": "root",
                "header_index": i,
                "header_name": "X-Test",
                "header_value": str(i),
            }
            for i in range(4)
        ],
        "message_recipients": [
            {"account_id": ACCOUNT, "message_id": message_id, "recipient_index": 0}
        ],
        "message_labels": [
            {
                "account_id": ACCOUNT, "message_id": message_id, "label_id": "INBOX",
                "first_seen_at": NOW, "last_seen_at": NOW,
            }
        ],
        "mime_parts": [
            {"account_id": ACCOUNT, "message_id": message_id, "part_path": "root"}
        ],
        "attachments": [],
    }


def _baseline(n: int) -> dict[str, list[dict[str, Any]]]:
    tables: dict[str, list[dict[str, Any]]] = {
        name: [] for name in MESSAGE_PROJECTION_TABLES
    }
    for index in range(n):
        message = _normalized_message(f"m-{index:04d}")
        for name in MESSAGE_PROJECTION_TABLES:
            tables[name].extend(message[name])
    return tables


def test_indexed_reducer_matches_reference_on_mixed_history_and_duplicate_events() -> None:
    baseline = _baseline(30)
    rng = random.Random(73)
    choices = (
        GmailHistoryEventType.LABEL_ADDED,
        GmailHistoryEventType.LABEL_REMOVED,
        GmailHistoryEventType.MESSAGE_ADDED,
        GmailHistoryEventType.MESSAGE_DELETED,
    )
    deltas: list[PreparedMessageDelta] = []
    for index in range(180):
        event_type = rng.choice(choices)
        message_id = f"m-{rng.randrange(36):04d}"
        label_id = f"L{rng.randrange(6)}" if event_type in {
            GmailHistoryEventType.LABEL_ADDED,
            GmailHistoryEventType.LABEL_REMOVED,
        } else None
        event = _event(index, event_type, message_id, label_id)
        rows = (
            _normalized_message(message_id)
            if event_type is GmailHistoryEventType.MESSAGE_ADDED
            else None
        )
        deltas.append(PreparedMessageDelta(event=event, normalized_rows=rows))
        if index % 13 == 0:
            deltas.append(deltas[-1])

    rng.shuffle(deltas)
    indexed = IndexedMessageProjectionReducer().reduce(baseline, deltas)
    original = MessageProjectionReducer().reduce(baseline, deltas)
    assert indexed == original
    assert len(baseline["messages"]) == 30


def test_indexed_reducer_rejects_foreign_message_rows() -> None:
    event = _event(1, GmailHistoryEventType.MESSAGE_ADDED, "m-1")
    invalid = _normalized_message("m-2")
    with pytest.raises(MessageProjectionReductionError, match="another message"):
        IndexedMessageProjectionReducer().reduce(
            _baseline(2),
            (PreparedMessageDelta(event=event, normalized_rows=invalid),),
        )


def test_indexed_reducer_avoids_full_list_scans_during_label_storm() -> None:
    baseline = _baseline(1000)
    deltas = [
        PreparedMessageDelta(
            event=_event(i, GmailHistoryEventType.LABEL_ADDED, f"m-{i % 1000:04d}", "STARRED")
        )
        for i in range(3000)
    ]
    result, ignored = IndexedMessageProjectionReducer().reduce(baseline, deltas)

    assert ignored == 0
    assert len(result["messages"]) == 1000
    assert len(result["message_headers"]) == 4000
    assert len(result["message_labels"]) == 2000
    assert len(baseline["message_labels"]) == 1000
