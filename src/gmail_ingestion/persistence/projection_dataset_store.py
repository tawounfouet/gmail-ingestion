"""Persistence adapters for normalized baselines and message projection candidates."""

from __future__ import annotations

import importlib
import json
import os
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from pyingestkit.artifacts.base import ArtifactStore
from pyingestkit.core.exceptions import StorageError

from gmail_ingestion.domain import GmailAccount
from gmail_ingestion.projection.message_delta import MESSAGE_PROJECTION_TABLES
from gmail_ingestion.schemas import NORMALIZED_SCHEMA_VERSION, TABLE_CONTRACTS, TABLE_SORT_KEYS

from .projection_store import LocalProjectionStore, ProjectionStoreError


class ProjectionDatasetError(RuntimeError):
    """Raised when baseline or projected normalized data cannot be persisted safely."""


@dataclass(frozen=True, slots=True)
class ProjectionBaseline:
    """Normalized baseline rows required for message-level projection."""

    account: GmailAccount
    tables: Mapping[str, Sequence[Mapping[str, Any]]]


@dataclass(frozen=True, slots=True)
class ProjectionDatasetWriteResult:
    """Physical evidence produced for one candidate message projection."""

    table_paths: Mapping[str, Path]
    manifest_path: Path
    row_counts: Mapping[str, int]


class NormalizedBaselineReader:
    """Read the stable normalized v1 baseline through the ArtifactStore abstraction."""

    def __init__(self, store: ArtifactStore, *, job_id: str = "gmail.normalized") -> None:
        self.store = store
        self.job_id = job_id

    def read(self, run_id: UUID, account_id: str) -> ProjectionBaseline:
        """Load the baseline account plus message projection tables from Parquet."""
        accounts = self._read_table(run_id, "accounts")
        matching = [row for row in accounts if row.get("account_id") == account_id]
        if len(matching) != 1:
            raise ProjectionDatasetError(
                f"Expected exactly one baseline account row for {account_id}, found {len(matching)}"
            )
        account_row = matching[0]
        account = GmailAccount(
            account_id=account_id,
            email_address=_required_string(account_row, "email_address"),
            messages_total=_optional_int(account_row.get("messages_total")),
            threads_total=_optional_int(account_row.get("threads_total")),
            history_id=_optional_string(account_row.get("history_id")),
        )
        tables = {
            name: self._read_table(run_id, name)
            for name in MESSAGE_PROJECTION_TABLES
        }
        return ProjectionBaseline(account=account, tables=tables)

    def _read_table(self, run_id: UUID, table_name: str) -> list[dict[str, Any]]:
        relative_path = f"candidate/normalized/{table_name}.parquet"
        try:
            raw = self.store.read_bytes(self.store.uri_for(self.job_id, run_id, relative_path))
        except StorageError as exc:
            raise ProjectionDatasetError(
                f"Missing normalized baseline table {table_name!r} for run {run_id}"
            ) from exc
        pa = importlib.import_module("pyarrow")
        pq = importlib.import_module("pyarrow.parquet")
        table = pq.read_table(pa.BufferReader(raw))
        rows = table.to_pylist()
        if not isinstance(rows, list):
            raise ProjectionDatasetError(f"Baseline table {table_name!r} is invalid")
        return [cast(dict[str, Any], row) for row in rows]


class LocalProjectionDatasetStore:
    """Write/read deterministic message projection Parquet under LocalProjectionStore."""

    def __init__(self, projection_store: LocalProjectionStore) -> None:
        self.projection_store = projection_store

    def write_candidate(
        self,
        account_id: str,
        projection_id: str,
        tables: Mapping[str, Sequence[Mapping[str, Any]]],
        *,
        logical_digest: str,
    ) -> ProjectionDatasetWriteResult:
        """Persist a complete candidate message projection before control-plane validation."""
        normalized = _validate_projection_tables(account_id, tables)
        root = self.projection_store.candidate_data_dir(account_id, projection_id)
        normalized_dir = root / "normalized"
        normalized_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        paths: dict[str, Path] = {}
        pa = importlib.import_module("pyarrow")
        pq = importlib.import_module("pyarrow.parquet")

        for name in MESSAGE_PROJECTION_TABLES:
            contract = TABLE_CONTRACTS[name]
            rows = normalized[name]
            schema = pa.schema(
                [
                    pa.field(
                        field.name,
                        _arrow_type(pa, field.type),
                        nullable=field.nullable,
                    )
                    for field in contract.fields
                ]
            )
            table = pa.Table.from_pylist(rows, schema=schema)
            path = normalized_dir / f"{name}.parquet"
            temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            try:
                pq.write_table(table, temp)
                os.replace(temp, path)
                try:
                    path.chmod(0o600)
                except OSError:
                    pass
            finally:
                temp.unlink(missing_ok=True)
            paths[name] = path

        row_counts = {name: len(normalized[name]) for name in MESSAGE_PROJECTION_TABLES}
        manifest = {
            "projection_schema_version": "1",
            "normalized_schema_version": NORMALIZED_SCHEMA_VERSION,
            "account_id": account_id,
            "projection_id": projection_id,
            "tables": list(MESSAGE_PROJECTION_TABLES),
            "row_counts": row_counts,
            "logical_digest": logical_digest,
            "status": "CANDIDATE",
        }
        manifest_path = root / "projection-data.json"
        _write_atomic_json(manifest_path, manifest)
        return ProjectionDatasetWriteResult(
            table_paths=paths,
            manifest_path=manifest_path,
            row_counts=row_counts,
        )

    def read_candidate(
        self,
        account_id: str,
        projection_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """Reload candidate message projection rows."""
        root = self.projection_store.candidate_data_dir(account_id, projection_id)
        return _read_projection_tables(root)

    def read_version(
        self,
        account_id: str,
        projection_id: str,
    ) -> dict[str, list[dict[str, Any]]]:
        """Reload immutable promoted message projection rows."""
        root = self.projection_store.version_data_dir(account_id, projection_id)
        return _read_projection_tables(root)


def _read_projection_tables(root: Path) -> dict[str, list[dict[str, Any]]]:
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")
    result: dict[str, list[dict[str, Any]]] = {}
    for name in MESSAGE_PROJECTION_TABLES:
        path = root / "normalized" / f"{name}.parquet"
        if not path.is_file():
            raise ProjectionDatasetError(f"Missing projected table {name!r}")
        rows = pq.read_table(pa.BufferReader(path.read_bytes())).to_pylist()
        result[name] = [cast(dict[str, Any], row) for row in rows]
    return result


def _validate_projection_tables(
    account_id: str,
    tables: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    normalized: dict[str, list[dict[str, Any]]] = {}
    message_ids: set[str] = set()

    for name in MESSAGE_PROJECTION_TABLES:
        rows = [dict(row) for row in tables.get(name, ())]
        for row in rows:
            if row.get("account_id") != account_id:
                raise ProjectionDatasetError(
                    f"{name} contains a row for another account"
                )
        rows.sort(
            key=lambda row: tuple(
                _sortable(row.get(field)) for field in TABLE_SORT_KEYS[name]
            )
        )
        _validate_primary_keys(name, rows)
        normalized[name] = rows
        if name == "messages":
            message_ids = {
                _required_string(row, "message_id")
                for row in rows
            }

    for name in MESSAGE_PROJECTION_TABLES:
        if name == "messages":
            continue
        for row in normalized[name]:
            message_id = _required_string(row, "message_id")
            if message_id not in message_ids:
                raise ProjectionDatasetError(
                    f"{name} contains child row for missing message {message_id!r}"
                )
    return normalized


def _validate_primary_keys(name: str, rows: Sequence[Mapping[str, Any]]) -> None:
    contract = TABLE_CONTRACTS[name]
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        key = tuple(_sortable(row.get(field)) for field in contract.primary_key)
        if key in seen:
            raise ProjectionDatasetError(f"{name} contains duplicate logical key {key!r}")
        seen.add(key)


def _write_atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(dict(payload), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ProjectionDatasetError(f"{key} must be a non-empty string")
    return value


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ProjectionDatasetError("integer field cannot be boolean")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ProjectionDatasetError("integer field is invalid") from exc


def _sortable(value: Any) -> str:
    if value is None:
        return ""
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        rendered = isoformat()
        if isinstance(rendered, str):
            return rendered
    return str(value)


def _arrow_type(pa: Any, type_name: str) -> Any:
    mapping = {
        "string": pa.string,
        "int32": pa.int32,
        "int64": pa.int64,
        "boolean": pa.bool_,
    }
    if type_name == "timestamp[us, UTC]":
        return pa.timestamp("us", tz="UTC")
    try:
        return mapping[type_name]()
    except KeyError as exc:
        raise ProjectionStoreError(
            f"Unsupported normalized field type {type_name!r}"
        ) from exc
