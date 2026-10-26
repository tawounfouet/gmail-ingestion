"""Pure message-level projection reducer."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from gmail_ingestion.domain import GmailHistoryEventType
from gmail_ingestion.schemas import TABLE_SORT_KEYS

from .message_delta import MESSAGE_PROJECTION_TABLES, PreparedMessageDelta


class MessageProjectionReductionError(ValueError):
    """Raised when message deltas cannot be reduced safely."""


class MessageProjectionReducer:
    """Reduce baseline message tables plus ordered message events into current-state rows."""

    def reduce(
        self,
        baseline: Mapping[str, Sequence[Mapping[str, Any]]],
        deltas: Sequence[PreparedMessageDelta],
    ) -> tuple[dict[str, list[dict[str, Any]]], int]:
        """Return deterministic current rows and the number of duplicate events ignored."""
        state = {
            name: [dict(row) for row in baseline.get(name, ())]
            for name in MESSAGE_PROJECTION_TABLES
        }
        seen_event_ids: set[str] = set()
        duplicates = 0

        for delta in sorted(deltas, key=_delta_sort_key):
            event = delta.event
            if event.event_id in seen_event_ids:
                duplicates += 1
                continue
            seen_event_ids.add(event.event_id)

            if event.event_type is GmailHistoryEventType.MESSAGE_ADDED:
                if delta.normalized_rows is None:
                    raise MessageProjectionReductionError(
                        f"MESSAGE_ADDED {event.event_id} requires normalized rows"
                    )
                self._replace_message(state, event.message_id, delta.normalized_rows)
            elif event.event_type is GmailHistoryEventType.MESSAGE_DELETED:
                if delta.normalized_rows is not None:
                    raise MessageProjectionReductionError(
                        f"MESSAGE_DELETED {event.event_id} must not carry normalized rows"
                    )
                self._remove_message(state, event.message_id)
            else:
                raise MessageProjectionReductionError(
                    f"Unsupported event type for 0.5.0b1: {event.event_type.value}"
                )

        return _sort_tables(state), duplicates

    @staticmethod
    def _replace_message(
        state: dict[str, list[dict[str, Any]]],
        message_id: str,
        normalized_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> None:
        for table_name in MESSAGE_PROJECTION_TABLES:
            incoming = [dict(row) for row in normalized_rows.get(table_name, ())]
            for row in incoming:
                if row.get("message_id") != message_id:
                    raise MessageProjectionReductionError(
                        f"{table_name} contains rows for another message"
                    )
            state[table_name] = [
                row for row in state[table_name] if row.get("message_id") != message_id
            ]
            state[table_name].extend(incoming)

    @staticmethod
    def _remove_message(
        state: dict[str, list[dict[str, Any]]],
        message_id: str,
    ) -> None:
        for table_name in MESSAGE_PROJECTION_TABLES:
            state[table_name] = [
                row for row in state[table_name] if row.get("message_id") != message_id
            ]


def _delta_sort_key(delta: PreparedMessageDelta) -> tuple[int, int, str]:
    try:
        history = int(delta.event.history_id)
    except ValueError as exc:
        raise MessageProjectionReductionError(
            f"History id must be numeric: {delta.event.history_id!r}"
        ) from exc
    return (history, delta.event.event_ordinal, delta.event.event_id)


def _sort_tables(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for name in MESSAGE_PROJECTION_TABLES:
        keys = TABLE_SORT_KEYS[name]
        result[name] = sorted(
            (dict(row) for row in tables.get(name, ())),
            key=lambda row: tuple(_sortable(row.get(field)) for field in keys),
        )
    return result


def _sortable(value: Any) -> str:
    if value is None:
        return ""
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        rendered = isoformat()
        if isinstance(rendered, str):
            return rendered
    return str(value)
