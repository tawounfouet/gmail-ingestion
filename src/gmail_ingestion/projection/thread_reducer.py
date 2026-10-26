"""Deterministic thread projection derived from current messages and recipients."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from gmail_ingestion.domain import GmailHistoryEvent


class ThreadProjectionError(ValueError):
    """Raised when current message rows cannot be aggregated into valid threads."""


def recompute_threads(
    account_id: str,
    messages: Sequence[Mapping[str, Any]],
    recipients: Sequence[Mapping[str, Any]],
    events: Sequence[GmailHistoryEvent],
) -> list[dict[str, Any]]:
    """Recompute current thread rows deterministically from current message state."""
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for message in messages:
        if message.get("account_id") != account_id:
            raise ThreadProjectionError("message belongs to another account")
        thread_id = _required_string(message.get("thread_id"), "thread_id")
        grouped[thread_id].append(message)

    message_to_thread = {
        _required_string(message.get("message_id"), "message_id"): _required_string(
            message.get("thread_id"), "thread_id"
        )
        for message in messages
    }
    participants: dict[str, set[str]] = defaultdict(set)
    for recipient in recipients:
        if recipient.get("account_id") != account_id:
            raise ThreadProjectionError("recipient belongs to another account")
        message_id = _required_string(recipient.get("message_id"), "message_id")
        thread_id = message_to_thread.get(message_id)
        email = recipient.get("email_address")
        if thread_id is not None and isinstance(email, str) and email:
            participants[thread_id].add(email.lower())

    event_history: dict[str, list[str]] = defaultdict(list)
    event_times: list[datetime] = []
    for event in events:
        if event.account_id != account_id:
            raise ThreadProjectionError("event belongs to another account")
        event_times.append(event.observed_at)
        if event.thread_id:
            event_history[event.thread_id].append(event.history_id)

    projection_time = _projection_time(messages, event_times)
    rows: list[dict[str, Any]] = []
    for thread_id, thread_messages in grouped.items():
        ordered = sorted(
            thread_messages,
            key=lambda row: (
                _datetime_or_min(row.get("internal_date")),
                _required_string(row.get("message_id"), "message_id"),
            ),
        )
        first = ordered[0]
        last = ordered[-1]
        histories = [
            str(row["history_id"])
            for row in thread_messages
            if row.get("history_id") is not None
        ]
        histories.extend(event_history.get(thread_id, []))
        rows.append(
            {
                "account_id": account_id,
                "thread_id": thread_id,
                "history_id": _max_history_id(histories),
                "snippet": last.get("snippet"),
                "message_count": len(thread_messages),
                "first_message_at": first.get("internal_date"),
                "last_message_at": last.get("internal_date"),
                "first_message_id": _required_string(first.get("message_id"), "message_id"),
                "last_message_id": _required_string(last.get("message_id"), "message_id"),
                "participant_count": len(participants.get(thread_id, set())),
                "has_attachments": any(
                    bool(row.get("has_attachments")) for row in thread_messages
                ),
                "is_deleted": False,
                "ingested_at": projection_time,
                "updated_at": projection_time,
            }
        )
    return sorted(rows, key=lambda row: (str(row["account_id"]), str(row["thread_id"])))


def _projection_time(
    messages: Sequence[Mapping[str, Any]],
    event_times: Sequence[datetime],
) -> datetime:
    candidates = list(event_times)
    for message in messages:
        for field in ("updated_at", "ingested_at"):
            value = message.get(field)
            if isinstance(value, datetime):
                candidates.append(value)
    if not candidates:
        return datetime(1970, 1, 1, tzinfo=UTC)
    if any(value.tzinfo is None or value.utcoffset() is None for value in candidates):
        raise ThreadProjectionError("thread projection timestamps must be timezone-aware")
    return max(value.astimezone(UTC) for value in candidates)


def _datetime_or_min(value: Any) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=UTC)
    if not isinstance(value, datetime):
        raise ThreadProjectionError("internal_date must be a datetime or null")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ThreadProjectionError("internal_date must be timezone-aware")
    return value.astimezone(UTC)


def _max_history_id(values: Sequence[str]) -> str | None:
    if not values:
        return None
    try:
        return str(max(int(value) for value in values))
    except ValueError as exc:
        raise ThreadProjectionError("history_id must be numeric") from exc


def _required_string(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ThreadProjectionError(f"{name} must be a non-empty string")
    return value
