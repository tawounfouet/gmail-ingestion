"""Parquet persistence for the normalized Gmail labels dataset."""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from uuid import UUID

from pyingestkit.artifacts.base import ArtifactStore
from pyingestkit.dataset import Dataset

LABEL_SCHEMA_ARTIFACT = {
    "dataset": "labels",
    "grain": "one row per account_id and label_id",
    "primary_key": ["account_id", "label_id"],
    "fields": [
        {"name": "account_id", "type": "string", "nullable": False},
        {"name": "label_id", "type": "string", "nullable": False},
        {"name": "name", "type": "string", "nullable": False},
        {"name": "label_type", "type": "string", "nullable": True},
        {"name": "message_list_visibility", "type": "string", "nullable": True},
        {"name": "label_list_visibility", "type": "string", "nullable": True},
        {"name": "messages_total", "type": "int64", "nullable": True},
        {"name": "messages_unread", "type": "int64", "nullable": True},
        {"name": "threads_total", "type": "int64", "nullable": True},
        {"name": "threads_unread", "type": "int64", "nullable": True},
        {"name": "color_text", "type": "string", "nullable": True},
        {"name": "color_background", "type": "string", "nullable": True},
        {"name": "is_system", "type": "boolean", "nullable": False},
        {"name": "ingested_at", "type": "timestamp[us, UTC]", "nullable": False},
    ],
}


@dataclass(frozen=True, slots=True)
class LabelsWriteResult:
    """Paths produced for the normalized labels candidate dataset."""

    parquet_path: Path
    schema_path: Path


class LabelsParquetWriter:
    """Write the stable labels schema as an atomic Parquet candidate."""

    def __init__(self, store: ArtifactStore, *, job_id: str = "gmail.labels") -> None:
        self.store = store
        self.job_id = job_id

    def write(self, run_id: UUID, dataset: Dataset) -> LabelsWriteResult:
        """Persist the normalized dataset and its explicit schema contract."""
        pa = importlib.import_module("pyarrow")
        pq = importlib.import_module("pyarrow.parquet")
        schema = pa.schema(
            [
                pa.field("account_id", pa.string(), nullable=False),
                pa.field("label_id", pa.string(), nullable=False),
                pa.field("name", pa.string(), nullable=False),
                pa.field("label_type", pa.string()),
                pa.field("message_list_visibility", pa.string()),
                pa.field("label_list_visibility", pa.string()),
                pa.field("messages_total", pa.int64()),
                pa.field("messages_unread", pa.int64()),
                pa.field("threads_total", pa.int64()),
                pa.field("threads_unread", pa.int64()),
                pa.field("color_text", pa.string()),
                pa.field("color_background", pa.string()),
                pa.field("is_system", pa.bool_(), nullable=False),
                pa.field("ingested_at", pa.timestamp("us", tz="UTC"), nullable=False),
            ]
        )
        table = pa.Table.from_pylist(dataset.to_rows(), schema=schema)
        path = self.store.path_for(self.job_id, run_id, "candidate/labels.parquet")
        temp = path.with_name(f".{path.name}.tmp")
        try:
            pq.write_table(table, temp)
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)

        schema_artifact = self.store.write_json_artifact(
            self.job_id,
            run_id,
            "candidate/labels.schema.json",
            LABEL_SCHEMA_ARTIFACT,
        )
        return LabelsWriteResult(
            parquet_path=path,
            schema_path=cast(Path, schema_artifact.local_path),
        )
