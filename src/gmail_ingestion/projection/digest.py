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
    canonical: dict[str, list[dict[str, Any]]] = {}
    for name in table_names:
        keys = TABLE_SORT_KEYS[name]
        rows = [
            {
                key: _json_value(value)
                for key, value in sorted(dict(row).items())
                if key not in _NON_LOGICAL_FIELDS
            }
            for row in tables.get(name, ())
        ]
        rows.sort(
            key=lambda row: tuple(str(row.get(field) or "") for field in keys)
        )
        canonical[name] = rows
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
