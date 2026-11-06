"""Indexed message reducer with O(1) per-message group updates.

A baseline may include millions of headers, so event reduction must not
repeatedly scan entire normalized tables. This implementation keeps each
message's related rows together without mutating input mappings, and delegates
final canonical sort/copy to the existing reducer helper.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from gmail_ingestion.domain import GmailHistoryEventType

from .message_delta import MESSAGE_PROJECTION_TABLES, PreparedMessageDelta
from .message_reducer import MessageProjectionReductionError, _delta_sort_key, _sort_tables


class IndexedMessageProjectionReducer:
    """Apply Gmail History deltas to per-message indexes, preserving v1 semantics."""

    def reduce(
        self,
        baseline: Mapping[str, Sequence[Mapping[str, Any]]],
        deltas: Sequence[PreparedMessageDelta],
    ) -> tuple[dict[str, list[dict[str, Any]]], int]:
        """Return canonical rows with no per-event scan over the full baseline."""
        groups: dict[str, dict[str, list[Mapping[str, Any]]]] = {
            name: {} for name in MESSAGE_PROJECTION_TABLES
        }
        for name in MESSAGE_PROJECTION_TABLES:
            index = groups[name]
            for row in baseline.get(name, ()):
                message_id = row.get("message_id")
                if not isinstance(message_id, str):
                    raise MessageProjectionReductionError(
                        f"{name} baseline row has invalid message_id"
                    )
                index.setdefault(message_id, []).append(row)

        seen_event_ids: set[str] = set()
        duplicates = 0
        for delta in sorted(deltas, key=_delta_sort_key):
            event = delta.event
            if event.event_id in seen_event_ids:
                duplicates += 1
                continue
            seen_event_ids.add(event.event_id)
            message_id = event.message_id
            if event.event_type is GmailHistoryEventType.MESSAGE_ADDED:
                if delta.normalized_rows is None:
                    raise MessageProjectionReductionError(
                        f"MESSAGE_ADDED {event.event_id} requires normalized rows"
                    )
                for name in MESSAGE_PROJECTION_TABLES:
                    incoming = delta.normalized_rows.get(name, ())
                    for row in incoming:
                        if row.get("message_id") != message_id:
                            raise MessageProjectionReductionError(
                                f"{name} contains rows for another message"
                            )
                    if incoming:
                        groups[name][message_id] = list(incoming)
                    else:
                        groups[name].pop(message_id, None)
            elif event.event_type is GmailHistoryEventType.MESSAGE_DELETED:
                if delta.normalized_rows is not None:
                    raise MessageProjectionReductionError(
                        f"MESSAGE_DELETED {event.event_id} must not carry normalized rows"
                    )
                for name in MESSAGE_PROJECTION_TABLES:
                    groups[name].pop(message_id, None)
            elif event.event_type is GmailHistoryEventType.LABEL_ADDED:
                if event.label_id is None:
                    raise MessageProjectionReductionError("LABEL_ADDED requires label_id")
                if not groups["messages"].get(message_id):
                    continue
                labels = groups["message_labels"].get(message_id, ())
                prior = next(
                    (row for row in labels if row.get("label_id") == event.label_id),
                    None,
                )
                first_seen_at = (
                    prior.get("first_seen_at")
                    if prior is not None
                    else event.observed_at
                )
                updated = [
                    row for row in labels if row.get("label_id") != event.label_id
                ]
                updated.append(
                    {
                        "account_id": event.account_id,
                        "message_id": message_id,
                        "label_id": event.label_id,
                        "added_history_id": event.history_id,
                        "removed_history_id": None,
                        "is_active": True,
                        "first_seen_at": first_seen_at,
                        "last_seen_at": event.observed_at,
                    }
                )
                groups["message_labels"][message_id] = updated
            elif event.event_type is GmailHistoryEventType.LABEL_REMOVED:
                if event.label_id is None:
                    raise MessageProjectionReductionError("LABEL_REMOVED requires label_id")
                prior_labels = groups["message_labels"].get(message_id)
                if prior_labels:
                    remaining = [
                        row for row in prior_labels if row.get("label_id") != event.label_id
                    ]
                    if remaining:
                        groups["message_labels"][message_id] = remaining
                    else:
                        groups["message_labels"].pop(message_id, None)
            else:
                raise MessageProjectionReductionError(
                    f"Unsupported projection event type: {event.event_type.value}"
                )

        result = {
            name: [
                row
                for batch in groups[name].values()
                for row in batch
            ]
            for name in MESSAGE_PROJECTION_TABLES
        }
        return _sort_tables(result), duplicates
