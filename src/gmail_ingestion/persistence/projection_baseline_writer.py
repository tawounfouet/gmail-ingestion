"""Streaming persistence for projection-compatible normalized baselines."""

from __future__ import annotations

import importlib
import json
import os
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from pyingestkit.artifacts.base import ArtifactStore
from pyingestkit.core.exceptions import StorageError

from gmail_ingestion.projection.message_delta import MESSAGE_PROJECTION_TABLES
from gmail_ingestion.schemas import NORMALIZED_SCHEMA_VERSION, TABLE_CONTRACTS, TABLE_SORT_KEYS

_JOB_ID = "gmail.projection_baseline"
_BASELINE_TABLES = ("accounts", *MESSAGE_PROJECTION_TABLES)


class ProjectionBaselineWriteError(RuntimeError):
    """Raised when a derived projection baseline cannot be persisted safely."""


@dataclass(frozen=True, slots=True)
class ProjectionBaselineWriteResult:
    """Evidence produced by one projection-baseline bootstrap."""

    run_id: UUID
    account_id: str
    history_id: str
    row_counts: Mapping[str, int]
    manifest_path: Path
    reused: bool


class ProjectionBaselineParquetWriter:
    """Write a projection-compatible normalized baseline without loading it all in memory."""

    def __init__(self, store: ArtifactStore, *, job_id: str = _JOB_ID) -> None:
        self.store = store
        self.job_id = job_id

    def write(
        self,
        *,
        run_id: UUID,
        account_id: str,
        history_id: str,
        account_row: Mapping[str, Any],
        batches: Iterable[Mapping[str, Sequence[Mapping[str, Any]]]],
        dataset_version: str,
        source_connector_version: str,
    ) -> ProjectionBaselineWriteResult:
        """Stream normalized rows into Parquet and publish a completion manifest last."""
        existing = self._read_manifest(run_id)
        if existing is not None:
            return self._result_from_manifest(run_id, existing, reused=True)

        pa = importlib.import_module("pyarrow")
        pq = importlib.import_module("pyarrow.parquet")
        final_paths = {
            name: self.store.path_for(
                self.job_id,
                run_id,
                f"normalized/{name}.parquet",
            )
            for name in _BASELINE_TABLES
        }
        temp_paths = {
            name: path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
            for name, path in final_paths.items()
        }
        writers: dict[str, Any] = {}
        counts = {name: 0 for name in _BASELINE_TABLES}

        try:
            for name in _BASELINE_TABLES:
                temp_paths[name].unlink(missing_ok=True)
                writers[name] = pq.ParquetWriter(
                    temp_paths[name],
                    _schema(pa, name),
                )

            account_payload = [dict(account_row)]
            writers["accounts"].write_table(
                pa.Table.from_pylist(account_payload, schema=_schema(pa, "accounts"))
            )
            counts["accounts"] = 1

            for batch in batches:
                for name in MESSAGE_PROJECTION_TABLES:
                    rows = [dict(row) for row in batch.get(name, ())]
                    if not rows:
                        continue
                    rows.sort(
                        key=lambda row: tuple(
                            _sortable(row.get(field)) for field in TABLE_SORT_KEYS[name]
                        )
                    )
                    writers[name].write_table(
                        pa.Table.from_pylist(rows, schema=_schema(pa, name))
                    )
                    counts[name] += len(rows)

            for writer in writers.values():
                writer.close()
            writers.clear()

            for name in _BASELINE_TABLES:
                os.replace(temp_paths[name], final_paths[name])
                try:
                    final_paths[name].chmod(0o600)
                except OSError:
                    pass

            manifest = {
                "projection_baseline_schema_version": 1,
                "normalized_schema_version": NORMALIZED_SCHEMA_VERSION,
                "status": "COMPLETED",
                "account_id": account_id,
                "source_snapshot_run_id": str(run_id),
                "history_id": history_id,
                "dataset_version": dataset_version,
                "source_connector_version": source_connector_version,
                "tables": list(_BASELINE_TABLES),
                "row_counts": counts,
            }
            artifact = self.store.write_json_artifact(
                self.job_id,
                run_id,
                "baseline_manifest.json",
                manifest,
            )
            return ProjectionBaselineWriteResult(
                run_id=run_id,
                account_id=account_id,
                history_id=history_id,
                row_counts=counts,
                manifest_path=artifact.local_path,
                reused=False,
            )
        except Exception as exc:  # noqa: BLE001 - persistence adapter boundary
            raise ProjectionBaselineWriteError(
                f"Unable to materialize projection baseline for run {run_id}"
            ) from exc
        finally:
            for writer in writers.values():
                try:
                    writer.close()
                except Exception:
                    pass
            for path in temp_paths.values():
                path.unlink(missing_ok=True)

    def _read_manifest(self, run_id: UUID) -> Mapping[str, Any] | None:
        try:
            raw = self.store.read_bytes(
                self.store.uri_for(self.job_id, run_id, "baseline_manifest.json")
            )
        except StorageError:
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProjectionBaselineWriteError(
                "Persisted projection baseline manifest is invalid"
            ) from exc
        if not isinstance(payload, dict):
            raise ProjectionBaselineWriteError(
                "Persisted projection baseline manifest must be an object"
            )
        if payload.get("status") != "COMPLETED":
            raise ProjectionBaselineWriteError(
                "Persisted projection baseline manifest is not COMPLETED"
            )
        return cast(Mapping[str, Any], payload)

    def _result_from_manifest(
        self,
        run_id: UUID,
        payload: Mapping[str, Any],
        *,
        reused: bool,
    ) -> ProjectionBaselineWriteResult:
        account_id = _required_string(payload, "account_id")
        history_id = _required_string(payload, "history_id")
        source_run_id = UUID(_required_string(payload, "source_snapshot_run_id"))
        if source_run_id != run_id:
            raise ProjectionBaselineWriteError(
                "Projection baseline manifest source run does not match requested run"
            )
        counts_raw = payload.get("row_counts")
        if not isinstance(counts_raw, Mapping):
            raise ProjectionBaselineWriteError("Projection baseline row_counts are missing")
        counts: dict[str, int] = {}
        for name in _BASELINE_TABLES:
            value = counts_raw.get(name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ProjectionBaselineWriteError(
                    f"Projection baseline row count for {name!r} is invalid"
                )
            counts[name] = value
        for name in _BASELINE_TABLES:
            path = self.store.path_for(
                self.job_id,
                run_id,
                f"normalized/{name}.parquet",
            )
            if not path.is_file():
                raise ProjectionBaselineWriteError(
                    f"Projection baseline manifest exists but {name!r} Parquet is missing"
                )
        manifest_path = self.store.path_for(
            self.job_id,
            run_id,
            "baseline_manifest.json",
        )
        return ProjectionBaselineWriteResult(
            run_id=run_id,
            account_id=account_id,
            history_id=history_id,
            row_counts=counts,
            manifest_path=manifest_path,
            reused=reused,
        )


def _schema(pa: Any, table_name: str) -> Any:
    contract = TABLE_CONTRACTS[table_name]
    return pa.schema(
        [
            pa.field(
                field.name,
                _arrow_type(pa, field.type),
                nullable=field.nullable,
            )
            for field in contract.fields
        ]
    )


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
        raise ProjectionBaselineWriteError(
            f"Unsupported normalized field type {type_name!r}"
        ) from exc


def _sortable(value: Any) -> str:
    if value is None:
        return ""
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        rendered = isoformat()
        if isinstance(rendered, str):
            return rendered
    return str(value)


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ProjectionBaselineWriteError(f"{key} must be a non-empty string")
    return value
