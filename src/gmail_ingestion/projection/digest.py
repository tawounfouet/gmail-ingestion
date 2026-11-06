"""Deterministic logical digest for materialized projection rows."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from gmail_ingestion.schemas import TABLE_SORT_KEYS

from .message_delta import MESSAGE_PROJECTION_TABLES

_NON_LOGICAL_FIELDS = frozenset(
    {
        "dataset_version",
        "source_connector_version",
        "ingested_at",
        "updated_at",
        "profile_fetched_at",
        "downloaded_at",
    }
)


def logical_projection_digest(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    table_names: Sequence[str] = MESSAGE_PROJECTION_TABLES,
) -> str:
    """Hash canonical logical rows independently of physical Parquet ordering."""
    # Reproduce json.dumps(canonical, sort_keys=True, separators=(",", ":"),
    # ensure_ascii=False) byte for byte without constructing a multi-GB
    # canonical payload or serializing every table at once.
    digest = hashlib.sha256()
    digest.update(b"{")
    for table_index, name in enumerate(sorted(table_names)):
        if table_index:
            digest.update(b",")
        digest.update(_compact_json(name).encode("utf-8"))
        digest.update(b":[")
        keys = TABLE_SORT_KEYS[name]
        ordered = sorted(
            tables.get(name, ()),
            key=lambda row: tuple(
                str(_json_value(row.get(field)) or "") for field in keys
            ),
        )
        for row_index, row in enumerate(ordered):
            if row_index:
                digest.update(b",")
            canonical_row = {
                key: _json_value(value)
                for key, value in sorted(dict(row).items())
                if key not in _NON_LOGICAL_FIELDS
            }
            digest.update(_compact_json(canonical_row).encode("utf-8"))
        digest.update(b"]")
    digest.update(b"}")
    return digest.hexdigest()


def _compact_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
