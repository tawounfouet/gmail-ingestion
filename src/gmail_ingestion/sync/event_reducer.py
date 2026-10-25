"""Pure reducers from Gmail History events to incremental mutation effects."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from gmail_ingestion.domain import GmailHistoryEvent, GmailHistoryEventType


class LabelMutationOperation(StrEnum):
    """Logical operation applied to the normalized message-label relation."""

    UPSERT_ACTIVE = "UPSERT_ACTIVE"
    DEACTIVATE = "DEACTIVATE"


@dataclass(frozen=True, slots=True)
class MessageTombstone:
    """Append-only evidence that Gmail deleted one message."""

    account_id: str
    message_id: str
    deletion_history_id: str
    deleted_at: datetime
    source_event_id: str
    run_id: UUID

    def to_payload(self) -> dict[str, str]:
        return {
            "account_id": self.account_id,
            "message_id": self.message_id,
            "deletion_history_id": self.deletion_history_id,
            "deleted_at": self.deleted_at.astimezone(UTC).isoformat(),
            "source_event_id": self.source_event_id,
            "run_id": str(self.run_id),
        }


@dataclass(frozen=True, slots=True)
class MessageLabelMutation:
    """One idempotent delta for the logical message_labels relation."""

    account_id: str
    message_id: str
    label_id: str
    operation: LabelMutationOperation
    history_id: str
    is_active: bool
    observed_at: datetime
    source_event_id: str
    run_id: UUID

    def to_payload(self) -> dict[str, str | bool]:
        return {
            "account_id": self.account_id,
            "message_id": self.message_id,
            "label_id": self.label_id,
            "operation": self.operation.value,
            "history_id": self.history_id,
            "is_active": self.is_active,
            "observed_at": self.observed_at.astimezone(UTC).isoformat(),
            "source_event_id": self.source_event_id,
            "run_id": str(self.run_id),
        }


@dataclass(frozen=True, slots=True)
class EventReduction:
    """Pure reducer result; the application layer persists the requested effects."""

    hydrate_message_id: str | None = None
    tombstone: MessageTombstone | None = None
    label_mutation: MessageLabelMutation | None = None


def reduce_history_event(event: GmailHistoryEvent) -> EventReduction:
    """Translate one normalized History event into exactly one business effect."""
    if event.event_type is GmailHistoryEventType.MESSAGE_ADDED:
        return EventReduction(hydrate_message_id=event.message_id)

    if event.event_type is GmailHistoryEventType.MESSAGE_DELETED:
        return EventReduction(
            tombstone=MessageTombstone(
                account_id=event.account_id,
                message_id=event.message_id,
                deletion_history_id=event.history_id,
                deleted_at=event.observed_at,
                source_event_id=event.event_id,
                run_id=event.run_id,
            )
        )

    if event.label_id is None:
        raise ValueError(f"{event.event_type.value} requires label_id")

    if event.event_type is GmailHistoryEventType.LABEL_ADDED:
        return EventReduction(
            label_mutation=MessageLabelMutation(
                account_id=event.account_id,
                message_id=event.message_id,
                label_id=event.label_id,
                operation=LabelMutationOperation.UPSERT_ACTIVE,
                history_id=event.history_id,
                is_active=True,
                observed_at=event.observed_at,
                source_event_id=event.event_id,
                run_id=event.run_id,
            )
        )

    if event.event_type is GmailHistoryEventType.LABEL_REMOVED:
        return EventReduction(
            label_mutation=MessageLabelMutation(
                account_id=event.account_id,
                message_id=event.message_id,
                label_id=event.label_id,
                operation=LabelMutationOperation.DEACTIVATE,
                history_id=event.history_id,
                is_active=False,
                observed_at=event.observed_at,
                source_event_id=event.event_id,
                run_id=event.run_id,
            )
        )

    raise ValueError(f"Unsupported Gmail History event type: {event.event_type!r}")
