"""Normalized Gmail History event domain model."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID


class GmailHistoryEventType(StrEnum):
    """Connector-level event names derived from Gmail History records."""

    MESSAGE_ADDED = "MESSAGE_ADDED"
    MESSAGE_DELETED = "MESSAGE_DELETED"
    LABEL_ADDED = "LABEL_ADDED"
    LABEL_REMOVED = "LABEL_REMOVED"


@dataclass(frozen=True, slots=True)
class GmailHistoryEvent:
    """One elementary normalized event emitted from a Gmail History record."""

    event_id: str
    account_id: str
    history_id: str
    event_type: GmailHistoryEventType
    message_id: str
    thread_id: str | None
    label_id: str | None
    event_ordinal: int
    source_page_token: str | None
    observed_at: datetime
    run_id: UUID

    def to_payload(self) -> dict[str, str | int | None]:
        """Return the stable JSON representation used by persistence."""
        return {
            "event_id": self.event_id,
            "account_id": self.account_id,
            "history_id": self.history_id,
            "event_type": self.event_type.value,
            "message_id": self.message_id,
            "thread_id": self.thread_id,
            "label_id": self.label_id,
            "event_ordinal": self.event_ordinal,
            "source_page_token": self.source_page_token,
            "observed_at": self.observed_at.astimezone(UTC).isoformat(),
            "run_id": str(self.run_id),
        }


class GmailHistoryMappingError(ValueError):
    """Raised when a Gmail History payload cannot satisfy the b1 event contract."""


def map_message_added_events(
    *,
    account_id: str,
    run_id: UUID,
    page: Mapping[str, Any],
    observed_at: datetime,
    source_page_token: str | None,
) -> tuple[GmailHistoryEvent, ...]:
    """Map only messagesAdded entries from one users.history.list response."""
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise GmailHistoryMappingError("observed_at must be timezone-aware")
    raw_history = page.get("history", [])
    if raw_history is None:
        return ()
    if not isinstance(raw_history, Sequence) or isinstance(raw_history, (str, bytes)):
        raise GmailHistoryMappingError("history must be a list")

    events: list[GmailHistoryEvent] = []
    for record in raw_history:
        if not isinstance(record, Mapping):
            raise GmailHistoryMappingError("history record must be an object")
        history_id = _required_string(record.get("id"), "history record id")
        additions = record.get("messagesAdded", [])
        if additions is None:
            continue
        if not isinstance(additions, Sequence) or isinstance(additions, (str, bytes)):
            raise GmailHistoryMappingError("messagesAdded must be a list")
        for ordinal, item in enumerate(additions):
            if not isinstance(item, Mapping):
                raise GmailHistoryMappingError("messagesAdded entry must be an object")
            message = item.get("message")
            if not isinstance(message, Mapping):
                raise GmailHistoryMappingError("messagesAdded.message must be an object")
            message_id = _required_string(message.get("id"), "message id")
            thread_id = _optional_string(message.get("threadId"))
            event_type = GmailHistoryEventType.MESSAGE_ADDED
            events.append(
                GmailHistoryEvent(
                    event_id=_event_id(
                        account_id=account_id,
                        history_id=history_id,
                        event_type=event_type,
                        message_id=message_id,
                        label_id=None,
                        event_ordinal=ordinal,
                    ),
                    account_id=account_id,
                    history_id=history_id,
                    event_type=event_type,
                    message_id=message_id,
                    thread_id=thread_id,
                    label_id=None,
                    event_ordinal=ordinal,
                    source_page_token=source_page_token,
                    observed_at=observed_at.astimezone(UTC),
                    run_id=run_id,
                )
            )
    return tuple(events)


def _event_id(
    *,
    account_id: str,
    history_id: str,
    event_type: GmailHistoryEventType,
    message_id: str,
    label_id: str | None,
    event_ordinal: int,
) -> str:
    natural_key = "|".join(
        (
            account_id,
            history_id,
            event_type.value,
            message_id,
            label_id or "",
            str(event_ordinal),
        )
    )
    return hashlib.sha256(natural_key.encode("utf-8")).hexdigest()


def _required_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise GmailHistoryMappingError(f"Missing or invalid {name}")
    return value


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
