"""Message-level projection delta contracts for 0.5.0b1."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gmail_ingestion.domain import GmailHistoryEvent

MESSAGE_PROJECTION_TABLES = (
    "messages",
    "message_headers",
    "message_recipients",
    "message_labels",
    "mime_parts",
    "attachments",
)


@dataclass(frozen=True, slots=True)
class PreparedMessageDelta:
    """One persisted Gmail message event prepared for pure projection reduction."""

    event: GmailHistoryEvent
    normalized_rows: Mapping[str, Sequence[Mapping[str, Any]]] | None = None


@dataclass(frozen=True, slots=True)
class MessageProjectionResult:
    """Outcome of one deterministic message-delta projection build."""

    projection_id: str
    input_history_id: str
    projected_history_id: str
    events_seen: int
    duplicate_events_ignored: int
    messages_added: int
    messages_deleted: int
    logical_digest: str
    row_counts: Mapping[str, int]
