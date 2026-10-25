"""Deterministic Parquet writer for normalized Gmail schema v1."""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pyingestkit.artifacts.base import ArtifactStore
from pyingestkit.dataset import Dataset

from gmail_ingestion.quality import validate_normalized_tables
from gmail_ingestion.schemas import NORMALIZED_SCHEMA_VERSION, TABLE_CONTRACTS, TABLE_SORT_KEYS


@dataclass(frozen=True, slots=True)
class NormalizedParquetWriteResult:
    """Physical paths produced by one normalized candidate write."""

    table_paths: dict[str, Path]
    schema_path: Path
    quality_path: Path


class NormalizedParquetWriter:
    """Write stable normalized tables under one candidate run namespace."""

    def __init__(self, store: ArtifactStore, *, job_id: str = "gmail.normalized") -> None:
        self.store = store
        self.job_id = job_id

    def write(
        self,
        run_id: UUID,
        tables: Mapping[str, Dataset],
    ) -> NormalizedParquetWriteResult:
        """Validate, sort and persist normalized tables plus schema/quality evidence."""
        rows = {name: dataset.to_rows() for name, dataset in tables.items()}
        report = validate_normalized_tables(rows)
        pa = importlib.import_module("pyarrow")
        pq = importlib.import_module("pyarrow.parquet")
        paths: dict[str, Path] = {}

        for name in sorted(tables):
            contract = TABLE_CONTRACTS[name]
            ordered = sorted(rows[name], key=_table_sorter(name))
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
            table = pa.Table.from_pylist(ordered, schema=schema)
            path = self.store.path_for(
                self.job_id,
                run_id,
                f"candidate/normalized/{name}.parquet",
            )
            temp = path.with_name(f".{path.name}.tmp")
            try:
                pq.write_table(table, temp)
                temp.replace(path)
            finally:
                temp.unlink(missing_ok=True)
            paths[name] = path

        schema_artifact = self.store.write_json_artifact(
            self.job_id,
            run_id,
            f"candidate/normalized/schema-v{NORMALIZED_SCHEMA_VERSION}.json",
            {
                "normalized_schema_version": NORMALIZED_SCHEMA_VERSION,
                "tables": {
                    name: {
                        "grain": contract.grain,
                        "primary_key": list(contract.primary_key),
                        "fields": [
                            {
                                "name": field.name,
                                "type": field.type,
                                "nullable": field.nullable,
                            }
                            for field in contract.fields
                        ],
                    }
                    for name, contract in TABLE_CONTRACTS.items()
                    if name in tables
                },
            },
        )
        quality_artifact = self.store.write_json_artifact(
            self.job_id,
            run_id,
            "candidate/normalized/quality.json",
            {
                "normalized_schema_version": report.normalized_schema_version,
                "row_counts": report.row_counts,
                "status": "PASSED",
            },
        )
        return NormalizedParquetWriteResult(
            table_paths=paths,
            schema_path=schema_artifact.local_path,
            quality_path=quality_artifact.local_path,
        )


def _table_sorter(name: str) -> Callable[[dict[str, Any]], tuple[str, ...]]:
    def key(row: dict[str, Any]) -> tuple[str, ...]:
        return _sort_key(name, row)

    return key


def _sort_key(name: str, row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(_sortable(row.get(field)) for field in TABLE_SORT_KEYS[name])


def _sortable(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
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
        raise ValueError(f"Unsupported normalized field type {type_name!r}") from exc
