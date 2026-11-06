"""Crash-resumable, segment-committed projection baseline persistence.

Each bounded RAW normalization batch becomes six immutable Parquet artifacts and
one segment manifest. The segment manifest is published LAST and is the sole
commit marker. The overall baseline manifest is published only after all
segments and the accounts table have been validated.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

from pyingestkit.artifacts.base import ArtifactStore

from gmail_ingestion.projection.message_delta import MESSAGE_PROJECTION_TABLES
from gmail_ingestion.schemas import NORMALIZED_SCHEMA_VERSION, TABLE_SORT_KEYS

from .projection_baseline_writer import (
    ProjectionBaselineWriteError,
    ProjectionBaselineWriteResult,
    _schema,
    _sortable,
)

_LAYOUT = "segmented-parquet-v1"
_JOB_ID = "gmail.projection_baseline"

BatchLoader = Callable[[int, int], Mapping[str, Sequence[Mapping[str, Any]]]]
ProgressCallback = Callable[[int, int], None]


class ResumableProjectionBaselineWriter:
    """Persist bounded Parquet segments with durable replay-safe checkpoints."""

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
        batch_size: int,
        total_messages: int,
        batch_loader: BatchLoader,
        dataset_version: str,
        source_connector_version: str,
        progress: ProgressCallback | None = None,
    ) -> ProjectionBaselineWriteResult:
        """Commit normalized batches one at a time and resume at last valid segment."""
        if batch_size < 1 or total_messages < 1:
            raise ProjectionBaselineWriteError("batch_size and total_messages must be positive")
        if not account_id or not history_id:
            raise ProjectionBaselineWriteError("account_id and history_id must not be empty")

        root = self.store.path_for(self.job_id, run_id, "baseline_manifest.json").parent
        segment_root = root / "segments"
        segment_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        expected = {
            "account_id": account_id,
            "history_id": history_id,
            "source_snapshot_run_id": str(run_id),
            "dataset_version": dataset_version,
            "source_connector_version": source_connector_version,
            "batch_size": batch_size,
            "total_messages": total_messages,
        }
        segment_count = (total_messages + batch_size - 1) // batch_size
        counts = {"accounts": 1, **{name: 0 for name in MESSAGE_PROJECTION_TABLES}}
        completed_segments = 0
        stage = "validate-resume"

        try:
            committed_indices = {
                int(path.parent.name)
                for path in segment_root.glob("*/segment.json")
                if path.parent.name.isdecimal()
            }
            for index in range(segment_count):
                start = index * batch_size
                stop = min(start + batch_size, total_messages)
                meta_path = segment_root / f"{index:08d}" / "segment.json"
                if not meta_path.is_file():
                    if any(later > index for later in committed_indices):
                        raise ProjectionBaselineWriteError(
                            "Segment manifest gap detected: refusing non-contiguous recovery"
                        )
                    break
                meta = _read_json(meta_path)
                self._verify_segment(meta_path.parent, meta, expected, index, start, stop)
                for name in MESSAGE_PROJECTION_TABLES:
                    counts[name] += _row_count(meta, name)
                completed_segments += 1

            if progress is not None and completed_segments:
                progress(min(completed_segments * batch_size, total_messages), total_messages)

            final_path = root / "baseline_manifest.json"
            if final_path.is_file():
                final = _read_json(final_path)
                _require_metadata(final, expected)
                if final.get("layout") != _LAYOUT or final.get("status") != "COMPLETED":
                    raise ProjectionBaselineWriteError("Completed baseline manifest has invalid layout/status")
                if completed_segments != segment_count:
                    raise ProjectionBaselineWriteError("Completed baseline has missing committed segments")
                if final.get("row_counts") != counts:
                    raise ProjectionBaselineWriteError("Completed baseline row counts mismatch")
                account_path = root / "normalized" / "accounts.parquet"
                if not account_path.is_file():
                    raise ProjectionBaselineWriteError("Completed baseline is missing accounts.parquet")
                _verify_accounts(account_path, account_id)
                return ProjectionBaselineWriteResult(
                    run_id=run_id, account_id=account_id, history_id=history_id,
                    row_counts=counts, manifest_path=final_path, reused=True,
                )

            pa = importlib.import_module("pyarrow")
            pq = importlib.import_module("pyarrow.parquet")
            for index in range(completed_segments, segment_count):
                start = index * batch_size
                stop = min(start + batch_size, total_messages)
                stage = "normalize-next-batch"
                rows = batch_loader(start, stop)
                if len(rows.get("messages", ())) != stop - start:
                    raise ProjectionBaselineWriteError(
                        "Normalized batch message count does not match its input range"
                    )
                directory = segment_root / f"{index:08d}"
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                row_counts: dict[str, int] = {}
                hashes: dict[str, str] = {}

                for name in MESSAGE_PROJECTION_TABLES:
                    stage = f"write-parquet:{name}"
                    table_rows = [dict(row) for row in rows.get(name, ())]
                    table_rows.sort(
                        key=lambda row: tuple(
                            _sortable(row.get(field)) for field in TABLE_SORT_KEYS[name]
                        )
                    )
                    table = pa.Table.from_pylist(table_rows, schema=_schema(pa, name))
                    path = directory / f"{name}.parquet"
                    _atomic_parquet(pq, table, path)
                    row_counts[name] = len(table_rows)
                    hashes[name] = _sha256_file(path)

                stage = "commit-segment"
                meta = {
                    "schema_version": 1, "layout": _LAYOUT, **expected,
                    "segment_index": index, "start": start, "stop": stop,
                    "row_counts": row_counts, "sha256": hashes,
                }
                _atomic_json(directory / "segment.json", meta)
                for name in MESSAGE_PROJECTION_TABLES:
                    counts[name] += row_counts[name]
                completed_segments += 1
                if progress is not None:
                    progress(stop, total_messages)

            stage = "publish-accounts"
            account_path = root / "normalized" / "accounts.parquet"
            account_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            account = pa.Table.from_pylist([dict(account_row)], schema=_schema(pa, "accounts"))
            _atomic_parquet(pq, account, account_path)
            _verify_accounts(account_path, account_id)

            stage = "publish-baseline"
            manifest = {
                "projection_baseline_schema_version": 1,
                "normalized_schema_version": NORMALIZED_SCHEMA_VERSION,
                "layout": _LAYOUT,
                "status": "COMPLETED",
                **expected,
                "segment_count": segment_count,
                "row_counts": counts,
                "tables": ["accounts", *MESSAGE_PROJECTION_TABLES],
            }
            _atomic_json(final_path, manifest)
            return ProjectionBaselineWriteResult(
                run_id=run_id, account_id=account_id, history_id=history_id,
                row_counts=counts, manifest_path=final_path, reused=False,
            )
        except Exception as exc:  # noqa: BLE001 - persistence boundary, safe metadata only
            safe_reason = (
                f", reason={exc}" if isinstance(exc, ProjectionBaselineWriteError) else ""
            )
            raise ProjectionBaselineWriteError(
                f"Resumable baseline failed (stage={stage}, committed_segments={completed_segments}, "
                f"committed_messages={min(completed_segments * batch_size, total_messages)}, "
                f"exception_type={type(exc).__name__}{safe_reason})"
            ) from exc

    @staticmethod
    def _verify_segment(
        directory: Path,
        meta: Mapping[str, Any],
        expected: Mapping[str, Any],
        index: int,
        start: int,
        stop: int,
    ) -> None:
        """Reject incomplete, mismatched or corrupted committed segments."""
        _require_metadata(meta, expected)
        if (
            meta.get("layout") != _LAYOUT
            or meta.get("schema_version") != 1
            or meta.get("segment_index") != index
            or meta.get("start") != start
            or meta.get("stop") != stop
        ):
            raise ProjectionBaselineWriteError("Committed segment frontier/identity mismatch")
        hashes = meta.get("sha256")
        if not isinstance(hashes, dict):
            raise ProjectionBaselineWriteError("Committed segment is missing hashes")
        for name in MESSAGE_PROJECTION_TABLES:
            path = directory / f"{name}.parquet"
            expected_hash = hashes.get(name)
            if not isinstance(expected_hash, str) or not path.is_file():
                raise ProjectionBaselineWriteError(
                    f"Committed segment is missing Parquet evidence for {name}"
                )
            if _sha256_file(path) != expected_hash:
                raise ProjectionBaselineWriteError(
                    f"Committed segment digest mismatch for {name}"
                )
            _row_count(meta, name)


def _row_count(meta: Mapping[str, Any], name: str) -> int:
    counts = meta.get("row_counts")
    if not isinstance(counts, dict):
        raise ProjectionBaselineWriteError("Segment row_counts must be an object")
    count = counts.get(name)
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise ProjectionBaselineWriteError(f"Invalid segment row count for {name}")
    return count


def _require_metadata(meta: Mapping[str, Any], expected: Mapping[str, Any]) -> None:
    for key, value in expected.items():
        if meta.get(key) != value:
            raise ProjectionBaselineWriteError(
                f"Baseline resume metadata mismatch for {key}"
            )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectionBaselineWriteError("Invalid persisted baseline manifest") from exc
    if not isinstance(payload, dict):
        raise ProjectionBaselineWriteError("Baseline manifest must be an object")
    return payload


def _atomic_parquet(pq: Any, table: Any, path: Path) -> None:
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


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
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
        temp.unlink(missing_ok=True)


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _verify_accounts(path: Path, account_id: str) -> None:
    pq = importlib.import_module("pyarrow.parquet")
    table = pq.read_table(path, columns=["account_id"])
    if table.num_rows != 1 or table.column("account_id").to_pylist() != [account_id]:
        raise ProjectionBaselineWriteError("Accounts Parquet identity mismatch")
