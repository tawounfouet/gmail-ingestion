"""Projection delta contracts for current-state materialization."""

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

CURRENT_PROJECTION_TABLES = (*MESSAGE_PROJECTION_TABLES, "threads")


@dataclass(frozen=True, slots=True)
class PreparedMessageDelta:
    """One persisted Gmail message event prepared for pure projection reduction."""

    event: GmailHistoryEvent
    normalized_rows: Mapping[str, Sequence[Mapping[str, Any]]] | None = None


@dataclass(frozen=True, slots=True)
class MessageProjectionResult:
    """Outcome of one deterministic current-state projection build."""

    projection_id: str
    input_history_id: str
    projected_history_id: str
    events_seen: int
    duplicate_events_ignored: int
    messages_added: int
    messages_deleted: int
    logical_digest: str
    row_counts: Mapping[str, int]
    labels_added: int = 0
    labels_removed: int = 0
