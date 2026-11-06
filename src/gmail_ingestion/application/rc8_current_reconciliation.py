"""RC8 source-aware Current overlay over an immutable historical projection.

Reconciliation is deliberately distinct from the strict historic reducer:
it may use separately proven current-source payloads for missing IDs and
prune rows absent from a scoped source inventory, but must preserve an
explicit recovery ledger. Neither path synthesizes Gmail History events.
No Current pointer is touched by these pure functions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from gmail_ingestion.domain import GmailHistoryEvent
from gmail_ingestion.projection.indexed_message_reducer import IndexedMessageProjectionReducer
from gmail_ingestion.projection.message_delta import (
    MESSAGE_PROJECTION_TABLES,
    PreparedMessageDelta,
)
from gmail_ingestion.projection.thread_reducer import recompute_threads


class RC8MaterializationError(RuntimeError):
    """Unproven or malformed recovery would contaminate source Current state."""


def reconcile_current_rows(
    *,
    account_id: str,
    baseline_tables: Mapping[str, Sequence[Mapping[str, Any]]],
    prepared_deltas: Sequence[PreparedMessageDelta],
    source_message_ids: set[str],
    current_recovery_rows: Mapping[
        str, Mapping[str, Sequence[Mapping[str, Any]]]
    ],
    committed_events: Sequence[GmailHistoryEvent],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    """Reduce History, apply a separately tracked current-source overlay, recompute threads.

    The resulting candidate is not a certified historical reconstruction and
    cannot be promoted without independent source label/thread/MIME checks.
    Every source-only ID must have complete, newly captured full RAW evidence.
    """
    rows, duplicates = IndexedMessageProjectionReducer().reduce(
        baseline_tables, prepared_deltas
    )
    projected_ids = {
        row.get("message_id") for row in rows["messages"]
    }
    if None in projected_ids or any(not isinstance(x, str) for x in projected_ids):
        raise RC8MaterializationError("Materialized messages have invalid IDs")
    missing_from_projection = source_message_ids - projected_ids
    if not missing_from_projection.issubset(current_recovery_rows):
        raise RC8MaterializationError(
            "Source-only current messages lack independently captured full RAW"
        )

    # Source overlays apply to message bundles, never to event history.
    overlay_ids = set(missing_from_projection)
    for message_id in overlay_ids:
        bundle = current_recovery_rows[message_id]
        message_rows = bundle.get("messages", ())
        if len(message_rows) != 1 or message_rows[0].get("message_id") != message_id:
            raise RC8MaterializationError(
                "Source recovery has no unique normalized message row"
            )
        for name in MESSAGE_PROJECTION_TABLES:
            extra = bundle.get(name, ())
            if any(
                item.get("message_id") != message_id or item.get("account_id") != account_id
                for item in extra
            ):
                raise RC8MaterializationError(
                    "Source recovery contains cross-message or cross-account rows"
                )
            rows[name].extend(dict(item) for item in extra)

    # This is explicitly a current-source correction, NOT a fabricated
    # MESSAGE_DELETED History event. Preserve the exception provenance elsewhere.
    removed = projected_ids - source_message_ids
    for name in MESSAGE_PROJECTION_TABLES:
        rows[name] = [
            item for item in rows[name]
            if item.get("message_id") in source_message_ids
        ]

    final_ids = {str(x["message_id"]) for x in rows["messages"]}
    if final_ids != source_message_ids or len(rows["messages"]) != len(source_message_ids):
        raise RC8MaterializationError("Current candidate ID set differs from source inventory")
    labels = rows["message_labels"]
    if any(
        label.get("is_active") is True
        and label.get("label_id") in {"SPAM", "TRASH"}
        for label in labels
    ):
        raise RC8MaterializationError(
            "Current source includes a message still labelled Spam or Trash"
        )

    # Recompute MIME graph references as a separate, deterministic gate.
    part_keys = {
        (str(row.get("message_id")), str(row.get("part_path")))
        for row in rows["mime_parts"]
    }
    if len(part_keys) != len(rows["mime_parts"]):
        raise RC8MaterializationError("Current candidate contains duplicate MIME part paths")
    for part in rows["mime_parts"]:
        parent = part.get("parent_part_path")
        if parent is not None and (str(part.get("message_id")), str(parent)) not in part_keys:
            raise RC8MaterializationError("MIME child references a missing parent part")
    for attachment in rows["attachments"]:
        key = (str(attachment.get("message_id")), str(attachment.get("part_path")))
        if key not in part_keys:
            raise RC8MaterializationError("Attachment refers to a missing MIME part")

    rows["threads"] = recompute_threads(
        account_id, rows["messages"], rows["message_recipients"], committed_events
    )
    return rows, {
        "source_message_count": len(source_message_ids),
        "source_only_recovered_count": len(overlay_ids),
        "outside_source_pruned_count": len(removed),
        "historical_event_ids_unchanged": len(committed_events),
        "duplicate_events_ignored": duplicates,
    }
