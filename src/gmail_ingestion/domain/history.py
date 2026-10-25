"""Normalized Gmail History event domain model."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
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


class HistoryProcessingStatus(StrEnum):
    """Durable reducer status for one normalized History event."""

    PENDING = "PENDING"
    PROCESSED = "PROCESSED"


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
    processed_at: datetime | None = None
    processing_status: HistoryProcessingStatus = HistoryProcessingStatus.PENDING

    def __post_init__(self) -> None:
        _require_aware("observed_at", self.observed_at)
        if self.processed_at is not None:
            _require_aware("processed_at", self.processed_at)
        if self.processing_status is HistoryProcessingStatus.PENDING:
            if self.processed_at is not None:
                raise ValueError("PENDING history event cannot have processed_at")
        elif self.processing_status is HistoryProcessingStatus.PROCESSED:
            if self.processed_at is None:
                raise ValueError("PROCESSED history event requires processed_at")

    def mark_processed(self, *, processed_at: datetime | None = None) -> GmailHistoryEvent:
        """Return the same logical event marked as durably reduced."""
        return replace(
            self,
            processing_status=HistoryProcessingStatus.PROCESSED,
            processed_at=processed_at or datetime.now(UTC),
        )

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
            "processed_at": (
                self.processed_at.astimezone(UTC).isoformat()
                if self.processed_at is not None
                else None
            ),
            "processing_status": self.processing_status.value,
            "run_id": str(self.run_id),
        }


class GmailHistoryMappingError(ValueError):
    """Raised when a Gmail History payload cannot satisfy the event contract."""


def map_history_events(
    *,
    account_id: str,
    run_id: UUID,
    page: Mapping[str, Any],
    observed_at: datetime,
    source_page_token: str | None,
) -> tuple[GmailHistoryEvent, ...]:
    """Map specific Gmail change arrays to elementary normalized events."""
    _require_aware("observed_at", observed_at)
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
        events.extend(
            _message_events(
                account_id=account_id,
                run_id=run_id,
                record=record,
                field="messagesAdded",
                history_id=history_id,
                event_type=GmailHistoryEventType.MESSAGE_ADDED,
                observed_at=observed_at,
                source_page_token=source_page_token,
            )
        )
        events.extend(
            _message_events(
                account_id=account_id,
                run_id=run_id,
                record=record,
                field="messagesDeleted",
                history_id=history_id,
                event_type=GmailHistoryEventType.MESSAGE_DELETED,
                observed_at=observed_at,
                source_page_token=source_page_token,
            )
        )
        events.extend(
            _label_events(
                account_id=account_id,
                run_id=run_id,
                record=record,
                field="labelsAdded",
                history_id=history_id,
                event_type=GmailHistoryEventType.LABEL_ADDED,
                observed_at=observed_at,
                source_page_token=source_page_token,
            )
        )
        events.extend(
            _label_events(
                account_id=account_id,
                run_id=run_id,
                record=record,
                field="labelsRemoved",
                history_id=history_id,
                event_type=GmailHistoryEventType.LABEL_REMOVED,
                observed_at=observed_at,
                source_page_token=source_page_token,
            )
        )
    return tuple(events)


def map_message_added_events(
    *,
    account_id: str,
    run_id: UUID,
    page: Mapping[str, Any],
    observed_at: datetime,
    source_page_token: str | None,
) -> tuple[GmailHistoryEvent, ...]:
    """Compatibility helper returning only MESSAGE_ADDED events."""
    return tuple(
        event
        for event in map_history_events(
            account_id=account_id,
            run_id=run_id,
            page=page,
            observed_at=observed_at,
            source_page_token=source_page_token,
        )
        if event.event_type is GmailHistoryEventType.MESSAGE_ADDED
    )


def _message_events(
    *,
    account_id: str,
    run_id: UUID,
    record: Mapping[str, Any],
    field: str,
    history_id: str,
    event_type: GmailHistoryEventType,
    observed_at: datetime,
    source_page_token: str | None,
) -> list[GmailHistoryEvent]:
    entries = _sequence_field(record, field)
    events: list[GmailHistoryEvent] = []
    for ordinal, item in enumerate(entries):
        if not isinstance(item, Mapping):
            raise GmailHistoryMappingError(f"{field} entry must be an object")
        message = item.get("message")
        if not isinstance(message, Mapping):
            raise GmailHistoryMappingError(f"{field}.message must be an object")
        message_id = _required_string(message.get("id"), "message id")
        thread_id = _optional_string(message.get("threadId"))
        events.append(
            _event(
                account_id=account_id,
                run_id=run_id,
                history_id=history_id,
                event_type=event_type,
                message_id=message_id,
                thread_id=thread_id,
                label_id=None,
                event_ordinal=ordinal,
                observed_at=observed_at,
                source_page_token=source_page_token,
            )
        )
    return events


def _label_events(
    *,
    account_id: str,
    run_id: UUID,
    record: Mapping[str, Any],
    field: str,
    history_id: str,
    event_type: GmailHistoryEventType,
    observed_at: datetime,
    source_page_token: str | None,
) -> list[GmailHistoryEvent]:
    entries = _sequence_field(record, field)
    events: list[GmailHistoryEvent] = []
    ordinal = 0
    for item in entries:
        if not isinstance(item, Mapping):
            raise GmailHistoryMappingError(f"{field} entry must be an object")
        message = item.get("message")
        if not isinstance(message, Mapping):
            raise GmailHistoryMappingError(f"{field}.message must be an object")
        message_id = _required_string(message.get("id"), "message id")
        thread_id = _optional_string(message.get("threadId"))
        label_ids = item.get("labelIds", [])
        if not isinstance(label_ids, Sequence) or isinstance(label_ids, (str, bytes)):
            raise GmailHistoryMappingError(f"{field}.labelIds must be a list")
        for raw_label_id in label_ids:
            label_id = _required_string(raw_label_id, "label id")
            events.append(
                _event(
                    account_id=account_id,
                    run_id=run_id,
                    history_id=history_id,
                    event_type=event_type,
                    message_id=message_id,
                    thread_id=thread_id,
                    label_id=label_id,
                    event_ordinal=ordinal,
                    observed_at=observed_at,
                    source_page_token=source_page_token,
                )
            )
            ordinal += 1
    return events


def _event(
    *,
    account_id: str,
    run_id: UUID,
    history_id: str,
    event_type: GmailHistoryEventType,
    message_id: str,
    thread_id: str | None,
    label_id: str | None,
    event_ordinal: int,
    observed_at: datetime,
    source_page_token: str | None,
) -> GmailHistoryEvent:
    return GmailHistoryEvent(
        event_id=_event_id(
            account_id=account_id,
            history_id=history_id,
            event_type=event_type,
            message_id=message_id,
            label_id=label_id,
            event_ordinal=event_ordinal,
        ),
        account_id=account_id,
        history_id=history_id,
        event_type=event_type,
        message_id=message_id,
        thread_id=thread_id,
        label_id=label_id,
        event_ordinal=event_ordinal,
        source_page_token=source_page_token,
        observed_at=observed_at.astimezone(UTC),
        run_id=run_id,
    )


def _sequence_field(record: Mapping[str, Any], field: str) -> Sequence[Any]:
    value = record.get(field, [])
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise GmailHistoryMappingError(f"{field} must be a list")
    return value


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


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise GmailHistoryMappingError(f"{name} must be timezone-aware")
