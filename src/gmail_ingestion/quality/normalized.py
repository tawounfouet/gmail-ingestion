"""Logical key and referential checks for normalized Gmail tables."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from gmail_ingestion.schemas import NORMALIZED_SCHEMA_VERSION, TABLE_CONTRACTS


class NormalizedQualityError(ValueError):
    """Raised when a normalized dataset violates the v1 contract."""


@dataclass(frozen=True, slots=True)
class NormalizedQualityReport:
    """Successful normalized quality evaluation."""

    row_counts: dict[str, int]
    normalized_schema_version: str = NORMALIZED_SCHEMA_VERSION


def validate_normalized_tables(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
) -> NormalizedQualityReport:
    """Validate logical keys, required fields and available foreign-key relations."""
    unknown = sorted(set(tables) - set(TABLE_CONTRACTS))
    if unknown:
        raise NormalizedQualityError(f"Unknown normalized tables: {', '.join(unknown)}")

    for name, rows in tables.items():
        _required_fields(name, rows)
        _unique_keys(name, rows)

    _message_schema_version(tables.get("messages", ()))
    _non_negative(tables.get("attachments", ()), "size_bytes", "attachments")
    _downloaded_attachments(tables.get("attachments", ()))

    _fk(
        tables,
        child="threads",
        child_fields=("account_id", "thread_id"),
        parent="messages",
        parent_fields=("account_id", "thread_id"),
        require_parent_key_not_unique=True,
    )
    for child in ("message_headers", "message_recipients", "mime_parts", "attachments"):
        _fk(
            tables,
            child=child,
            child_fields=("account_id", "message_id"),
            parent="messages",
            parent_fields=("account_id", "message_id"),
        )
    _fk(
        tables,
        child="message_labels",
        child_fields=("account_id", "message_id"),
        parent="messages",
        parent_fields=("account_id", "message_id"),
    )
    _fk(
        tables,
        child="message_labels",
        child_fields=("account_id", "label_id"),
        parent="labels",
        parent_fields=("account_id", "label_id"),
    )
    _fk(
        tables,
        child="messages",
        child_fields=("account_id", "thread_id"),
        parent="threads",
        parent_fields=("account_id", "thread_id"),
    )

    return NormalizedQualityReport(
        row_counts={name: len(rows) for name, rows in tables.items()}
    )


def _required_fields(name: str, rows: Sequence[Mapping[str, Any]]) -> None:
    contract = TABLE_CONTRACTS[name]
    required = tuple(field.name for field in contract.fields if not field.nullable)
    for index, row in enumerate(rows):
        for field in required:
            if row.get(field) is None:
                raise NormalizedQualityError(
                    f"{name}[{index}] requires non-null field {field}"
                )


def _unique_keys(name: str, rows: Sequence[Mapping[str, Any]]) -> None:
    contract = TABLE_CONTRACTS[name]
    seen: set[tuple[Any, ...]] = set()
    for index, row in enumerate(rows):
        if name == "attachments" and row.get("attachment_id") is None:
            key = (
                row.get("account_id"),
                row.get("message_id"),
                row.get("part_path"),
            )
        else:
            key = tuple(row.get(field) for field in contract.primary_key)
        if key in seen:
            raise NormalizedQualityError(
                f"{name}[{index}] duplicates logical key {key!r}"
            )
        seen.add(key)


def _fk(
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    child: str,
    child_fields: tuple[str, ...],
    parent: str,
    parent_fields: tuple[str, ...],
    require_parent_key_not_unique: bool = False,
) -> None:
    if child not in tables or parent not in tables:
        return
    parent_keys = {
        tuple(row.get(field) for field in parent_fields)
        for row in tables[parent]
    }
    for index, row in enumerate(tables[child]):
        key = tuple(row.get(field) for field in child_fields)
        if key not in parent_keys:
            relation = " -> ".join((child, parent))
            raise NormalizedQualityError(
                f"{relation} broken at {child}[{index}] for key {key!r}"
            )
    _ = require_parent_key_not_unique


def _message_schema_version(rows: Sequence[Mapping[str, Any]]) -> None:
    for index, row in enumerate(rows):
        if row.get("normalized_schema_version") != NORMALIZED_SCHEMA_VERSION:
            raise NormalizedQualityError(
                f"messages[{index}] has unsupported normalized_schema_version"
            )


def _non_negative(
    rows: Sequence[Mapping[str, Any]],
    field: str,
    name: str,
) -> None:
    for index, row in enumerate(rows):
        value = row.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise NormalizedQualityError(
                f"{name}[{index}].{field} must be a non-negative integer"
            )


def _downloaded_attachments(rows: Sequence[Mapping[str, Any]]) -> None:
    for index, row in enumerate(rows):
        if row.get("download_status") != "DOWNLOADED":
            continue
        if not row.get("storage_uri"):
            raise NormalizedQualityError(
                f"attachments[{index}] DOWNLOADED requires storage_uri"
            )
        if not row.get("content_hash_sha256"):
            raise NormalizedQualityError(
                f"attachments[{index}] DOWNLOADED requires content_hash_sha256"
            )
