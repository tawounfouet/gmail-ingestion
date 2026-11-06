"""Ensure streaming canonical digest is byte-identical to the legacy JSON contract."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from gmail_ingestion.projection.digest import logical_projection_digest
from gmail_ingestion.schemas import TABLE_SORT_KEYS

_NON_LOGICAL_FIELDS = frozenset({
    "dataset_version", "source_connector_version", "ingested_at",
    "updated_at", "profile_fetched_at", "downloaded_at",
})


def _reference_digest(
    tables: dict[str, list[dict[str, Any]]],
    names: tuple[str, ...],
) -> str:
    canonical: dict[str, list[dict[str, Any]]] = {}
    for name in names:
        rows = [
            {
                key: value.isoformat() if isinstance(value, datetime) else
                     value if isinstance(value, (str, int, float, bool)) or value is None else
                     str(value)
                for key, value in sorted(dict(row).items())
                if key not in _NON_LOGICAL_FIELDS
            }
            for row in tables.get(name, ())
        ]
        keys = TABLE_SORT_KEYS[name]
        rows.sort(key=lambda row: tuple(str(row.get(field) or "") for field in keys))
        canonical[name] = rows
    return hashlib.sha256(
        json.dumps(
            canonical,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()


def test_streaming_digest_matches_whole_payload_for_unicode_and_nullable_keys() -> None:
    now = datetime(2026, 11, 6, 10, tzinfo=UTC)
    tables = {
        "messages": [
            {
                "account_id": "a",
                "message_id": "m2",
                "subject": "Résumé café ☕",
                "ingested_at": now,
                "dataset_version": "run-new",
                "size_estimate": 0,
            },
            {
                "account_id": "a",
                "message_id": "m1",
                "subject": None,
                "updated_at": now,
                "internal_date": now,
            },
        ],
        "message_headers": [
            {
                "account_id": "a", "message_id": "m1",
                "part_id": "root", "header_index": 3,
                "header_name": "Subject", "header_value": "áéîõü",
            },
            {
                "account_id": "a", "message_id": "m1",
                "part_id": "root", "header_index": 0,
                "header_name": "From", "header_value": "me@example.com",
            },
        ],
        "message_labels": [
            {"account_id": "a", "message_id": "m2", "label_id": "INBOX", "is_active": True},
        ],
    }
    names = ("messages", "message_headers", "message_labels")
    assert logical_projection_digest(tables, table_names=names) == _reference_digest(
        tables, names
    )


def test_streaming_digest_ignores_nonlogical_metadata_exactly() -> None:
    base = {"messages": [{"account_id": "a", "message_id": "m", "subject": "Hello"}]}
    modified = {
        "messages": [
            {
                **base["messages"][0],
                "dataset_version": "v2",
                "source_connector_version": "release-new",
                "ingested_at": datetime(2026, 11, 6, tzinfo=UTC),
            }
        ]
    }
    original = logical_projection_digest(base, table_names=("messages",))
    assert original == logical_projection_digest(modified, table_names=("messages",))
    assert original == _reference_digest(base, ("messages",))
