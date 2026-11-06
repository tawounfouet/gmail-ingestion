"""Deterministic source-to-Current parity for one Gmail FULL message bundle.

Comparison is intentionally logical: physical ingestion timestamps, dataset
version, attachment-download state and Gmail label first-seen bookkeeping
are not part of source equality. MIME/attachment structure, message fields,
label membership and thread references are checked.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from gmail_ingestion.projection.message_delta import MESSAGE_PROJECTION_TABLES

_IGNORE = frozenset({
    "dataset_version", "source_connector_version", "ingested_at",
    "updated_at", "downloaded_at", "profile_fetched_at",
    "added_history_id", "removed_history_id", "first_seen_at", "last_seen_at",
    "content_hash_sha256", "storage_uri", "download_status",
})


class RC8SourceParityError(RuntimeError):
    """A source message is not represented by the exact logical Current rows."""


def _logical(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, float, int, bool)) or value is None:
        return value
    return str(value)


def table_digest(name: str, rows: Sequence[Mapping[str, Any]]) -> str:
    """Hash normalized logical rows without emitting private message data."""
    if name not in MESSAGE_PROJECTION_TABLES:
        raise RC8SourceParityError("Unknown Current message table")
    canonical: list[str] = []
    for row in rows:
        data = {
            key: _logical(value)
            for key, value in row.items() if key not in _IGNORE
        }
        canonical.append(json.dumps(
            data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ))
    canonical.sort()
    return hashlib.sha256("\n".join(canonical).encode("utf-8")).hexdigest()


def compare_message_bundle(
    *,
    message_id: str,
    source_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    current_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    thread_ids: set[str],
) -> dict[str, bool]:
    """Compare all message tables and verify thread reference is materialized.

    Callers must first normalize the independent Gmail FULL payload and load
    the candidate's same-ID tables from immutable Parquet evidence.
    """
    if not message_id or not thread_ids:
        raise RC8SourceParityError("Missing source message or Current thread context")
    coverage: dict[str, bool] = {}
    for table in MESSAGE_PROJECTION_TABLES:
        left = source_rows.get(table, ())
        right = current_rows.get(table, ())
        if any(row.get("message_id") != message_id for row in (*left, *right)):
            raise RC8SourceParityError("Cross-message source or Current relation")
        if len(left) != len(right) or table_digest(table, left) != table_digest(table, right):
            raise RC8SourceParityError(f"Current mismatch in {table}")
        coverage[table] = True
    msgs = current_rows.get("messages", ())
    if len(msgs) != 1:
        raise RC8SourceParityError("Current message row must be unique")
    thread_id = msgs[0].get("thread_id")
    if not isinstance(thread_id, str) or thread_id not in thread_ids:
        raise RC8SourceParityError("Current message references a missing thread")
    coverage["thread_reference"] = True
    return coverage
