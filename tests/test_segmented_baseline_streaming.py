"""Bounded, hash-qualified iteration of a segmented Gmail baseline."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from gmail_ingestion.persistence.projection_dataset_store import (
    NormalizedBaselineReader,
    ProjectionDatasetError,
)


RUN = UUID("00000000-0000-0000-0000-000000009901")
TABLE = "message_headers"


class _Store:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = payloads
        self.reads: list[str] = []

    def uri_for(self, job_id: str, run_id: UUID, relative: str) -> str:
        assert run_id == RUN
        return job_id + "/" + relative

    def read_bytes(self, uri: str) -> bytes:
        self.reads.append(uri)
        return self.payloads[uri]


def _fixture_data(
    *,
    corrupt_index: int | None = None,
    manifest_row_count: int = 5,
    status: str = "COMPLETED",
) -> tuple[_Store, list[dict[str, Any]]]:
    job = "gmail.projection_baseline"
    rows = [
        {
            "account_id": "acct_test",
            "message_id": "message-" + str(i // 3),
            "part_id": "",
            "header_index": i,
            "header_value": f"header {i}",
        }
        for i in range(5)
    ]
    payloads: dict[str, bytes] = {}
    for index, group in enumerate((rows[:3], rows[3:])):
        stream = io.BytesIO()
        pq.write_table(pa.Table.from_pylist(group), stream)
        raw = stream.getvalue()
        digest = hashlib.sha256(raw).hexdigest()
        if corrupt_index == index:
            raw += b"broken"
        prefix = f"{job}/segments/{index:08d}"
        payloads[f"{prefix}/{TABLE}.parquet"] = raw
        payloads[f"{prefix}/segment.json"] = json.dumps(
            {
                "layout": "segmented-parquet-v1",
                "source_snapshot_run_id": str(RUN),
                "segment_index": index,
                "start": index * 2,
                "stop": min((index + 1) * 2, 4),
                "sha256": {TABLE: digest},
                "row_counts": {TABLE: len(group)},
            }
        ).encode()
    payloads[job + "/baseline_manifest.json"] = json.dumps(
        {
            "layout": "segmented-parquet-v1",
            "status": status,
            "source_snapshot_run_id": str(RUN),
            "segment_count": 2,
            "batch_size": 2,
            "total_messages": 4,
            "row_counts": {TABLE: manifest_row_count},
        }
    ).encode()
    return _Store(payloads), rows


def test_iterator_yields_bounded_rows_and_preserves_existing_eager_result() -> None:
    store, rows = _fixture_data()
    reader = NormalizedBaselineReader(store)
    iterator = reader.iter_verified_segmented_table(
        RUN, TABLE, max_rows_per_batch=2
    )
    assert store.reads == []
    first = next(iterator)
    assert len(first) <= 2
    assert not any("00000001" in path for path in store.reads)
    remaining = list(iterator)
    assert all(0 < len(chunk) <= 2 for chunk in remaining)
    assert first + [row for chunk in remaining for row in chunk] == rows
    assert reader._read_segmented_table(RUN, TABLE) == rows


def test_iterator_rejects_corrupted_later_segment_before_emitting_its_rows() -> None:
    store, rows = _fixture_data(corrupt_index=1)
    reader = NormalizedBaselineReader(store)
    collected: list[dict[str, Any]] = []
    with pytest.raises(ProjectionDatasetError, match="digest/frontier mismatch"):
        for chunk in reader.iter_verified_segmented_table(RUN, TABLE):
            collected.extend(chunk)
    assert collected == rows[:3]


def test_iterator_rejects_wrong_total_count_on_exhaustion() -> None:
    store, rows = _fixture_data(manifest_row_count=6)
    reader = NormalizedBaselineReader(store)
    collected: list[dict[str, Any]] = []
    with pytest.raises(ProjectionDatasetError, match="total row count mismatch"):
        for chunk in reader.iter_verified_segmented_table(RUN, TABLE):
            collected.extend(chunk)
    assert collected == rows


@pytest.mark.parametrize("value", [0, -1, 65537, True])
def test_batch_bounds_fail_closed_before_reading(value: int) -> None:
    store, _ = _fixture_data()
    reader = NormalizedBaselineReader(store)
    with pytest.raises(ProjectionDatasetError, match="batch row limit"):
        list(reader.iter_verified_segmented_table(
            RUN, TABLE, max_rows_per_batch=value
        ))
    assert store.reads == []


def test_incomplete_baseline_rejected_without_rows() -> None:
    store, _ = _fixture_data(status="STARTED")
    reader = NormalizedBaselineReader(store)
    with pytest.raises(ProjectionDatasetError, match="not completed"):
        list(reader.iter_verified_segmented_table(RUN, TABLE))
