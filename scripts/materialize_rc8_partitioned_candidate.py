#!/usr/bin/env python3
"""Build isolated RC8 Current Parquet from verified segments with bounded RAM.

The candidate remains NOT source-qualified. The historical Gmail artifacts,
Current pointer, and strict RC7 projection are never modified or promoted.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import psutil
from materialize_rc8_current_candidate import (
    _prepared_events,
    _raw_recovery,
    _source_manifest,
    require_resource_headroom,
)
from observe_current_source_inventory import write_private_inventory
from plan_rc8_current_candidate import _source, build_candidate_plan
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion import __version__
from gmail_ingestion.application.rc8_disk_projection import DiskCurrentProjection
from gmail_ingestion.normalization import normalize_snapshot
from gmail_ingestion.persistence import (
    LocalProjectionBatchReader,
    LocalProjectionStore,
    RawMessageWriter,
)
from gmail_ingestion.persistence.projection_dataset_store import NormalizedBaselineReader
from gmail_ingestion.projection import validate_delta_chain
from gmail_ingestion.projection.message_delta import (
    CURRENT_PROJECTION_TABLES,
    MESSAGE_PROJECTION_TABLES,
)
from gmail_ingestion.schemas import NORMALIZED_SCHEMA_VERSION


class RC8PartitionedBuildError(RuntimeError):
    """Failed fail-closed partitioned Current projection qualification."""


def _resource_sample(
    filesystem: Path, *, min_free_gib: int, min_available_ram_gib: int
) -> dict[str, float]:
    current = require_resource_headroom(
        filesystem, min_free_gib=min_free_gib,
        min_available_ram_gib=min_available_ram_gib,
    )
    current["rss_gib"] = round(
        psutil.Process().memory_info().rss / 1024**3, 2
    )
    return current


def _identity(plan: dict[str, Any], recovery_manifest: dict[str, Any]) -> str:
    capture_digest = hashlib.sha256(
        json.dumps(
            recovery_manifest["content_sha256"], sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    identity = {
        "policy": "RC8_CURRENT_OVERLAY_V1",
        "plan_id": plan["candidate_id"],
        "source_history_id": plan["source_history_id"],
        "source_id_set_sha256": plan["source_id_set_sha256"],
        "recovery_run_id": recovery_manifest["run_id"],
        "recovery_content_digest": capture_digest,
    }
    return "proj_" + hashlib.sha256(
        json.dumps(
            identity, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()[:32]


def _source_payloads(
    account: Any, raw: dict[str, Any], projection_id: str
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    result: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for mid, message in raw.items():
        norm = normalize_snapshot(
            account, (), (message,), ingested_at=datetime(1970, 1, 1, tzinfo=UTC),
            dataset_version=projection_id, source_connector_version=__version__,
        )
        result[mid] = {
            name: norm.rows(name) for name in MESSAGE_PROJECTION_TABLES
        }
    return result


def build_partitioned_candidate(
    *, artifact_root: Path, projection_root: Path, account_id: str,
    baseline_run_id: UUID, delta_run_ids: tuple[UUID, ...],
    source_inventory: Path, recovery_run_id: UUID,
    min_free_gib: int = 40, min_available_ram_gib: int = 24,
    batch_size: int = 2048,
) -> dict[str, Any]:
    if not 1 <= batch_size <= 16384:
        raise RC8PartitionedBuildError("Invalid bounded row batch size")
    artifact_root = artifact_root.resolve()
    projection_root = projection_root.resolve()
    if projection_root == artifact_root or artifact_root in projection_root.parents:
        raise RC8PartitionedBuildError("Candidate projection root not isolated")
    if projection_root.name in {
        ".gmail-ingestion-control", ".gmail-ingestion-control-live-0.5.0"
    }:
        raise RC8PartitionedBuildError("Candidate output root is a control plane")
    if not projection_root.is_dir():
        raise RC8PartitionedBuildError("Create an empty output root first")
    initial_resources = _resource_sample(
        projection_root, min_free_gib=min_free_gib,
        min_available_ram_gib=min_available_ram_gib,
    )

    source_ids, source_doc = _source(source_inventory)
    if source_doc.get("account_id") != account_id:
        raise RC8PartitionedBuildError("Scoped source account mismatch")
    plan = build_candidate_plan(
        artifact_root=artifact_root, account_id=account_id,
        baseline_run_id=baseline_run_id, delta_run_ids=delta_run_ids,
        source_inventory=source_inventory, min_free_gib=min_free_gib,
    )
    if plan["source_id_set_sha256"] != source_doc["id_set_sha256"]:
        raise RC8PartitionedBuildError("Source set identity mismatch")
    store = LocalArtifactStore(artifact_root)
    recovered_manifest = _source_manifest(store, recovery_run_id, plan)
    recovery = _raw_recovery(store, recovery_run_id, recovered_manifest)
    projection_id = _identity(plan, recovered_manifest)

    control = LocalProjectionStore(projection_root)
    if control.load_current(account_id) is not None:
        raise RC8PartitionedBuildError("Authoritative Current pointer present")
    candidate_dir = control.candidate_data_dir(account_id, projection_id)
    if any(candidate_dir.iterdir()):
        raise RC8PartitionedBuildError("Candidate data already exists")
    work_root = projection_root / ("work-" + projection_id)
    if work_root.exists():
        raise RC8PartitionedBuildError("Existing immutable scratch root")
    work_root.mkdir(mode=0o700)

    baseline_reader = NormalizedBaselineReader(store)
    account = baseline_reader.read_account(baseline_run_id, account_id)
    batches = LocalProjectionBatchReader(store).read_many(delta_run_ids)
    all_events = validate_delta_chain(
        account_id=account_id, start_history_id=str(account.history_id),
        target_history_id=batches[-1].output_history_id, batches=batches,
    )
    events = [
        event for event in all_events
        if int(event.history_id) <= int(plan["source_history_id"])
    ]
    recovery_rows = _source_payloads(account, recovery, projection_id)
    prepared, substitutions = _prepared_events(
        account=account, plan=plan,
        original_raw=RawMessageWriter(store), recovery=recovery,
        events=events, source_ids=source_ids, projection_id=projection_id,
    )
    peak_rss = 0.0
    checkpoints: dict[str, dict[str, float]] = {}

    def guard(phase: str) -> None:
        nonlocal peak_rss
        sample = _resource_sample(
            projection_root, min_free_gib=min_free_gib,
            min_available_ram_gib=min_available_ram_gib,
        )
        peak_rss = max(peak_rss, sample["rss_gib"])
        checkpoints[phase] = sample

    def run_pass(
        database: Path, *, replay: bool, write: bool,
    ) -> tuple[str, dict[str, int], dict[str, int], dict[str, str]]:
        disk = DiskCurrentProjection(database, account_id)
        try:
            guard("replay_start" if replay else "build_start")
            observed = disk.ingest_baseline(
                baseline_reader, baseline_run_id, max_rows_per_batch=batch_size
            )
            guard("replay_after_baseline" if replay else "build_after_baseline")
            delta_result = disk.apply_deltas(
                tuple(reversed(prepared)) if replay else prepared
            )
            scope_result = disk.reconcile_scope(source_ids, recovery_rows)
            disk.finalize(source_ids, events)
            guard("replay_after_reduce" if replay else "build_after_reduce")
            counts = disk.row_counts()
            if counts["messages"] != len(source_ids):
                raise RC8PartitionedBuildError("Current source ID count mismatch")
            digest = disk.logical_digest()
            guard("replay_after_digest" if replay else "build_after_digest")
            physical: dict[str, str] = {}
            if write:
                physical = disk.write_parquet(
                    candidate_dir / "normalized", batch_size=batch_size
                )
                guard("build_after_parquet")
            return (
                digest, counts,
                observed | delta_result | scope_result, physical,
            )
        finally:
            disk.close()
            gc.collect()

    digest, counts, diagnostics, physical = run_pass(
        work_root / "build.sqlite3", replay=False, write=True
    )
    first_digest = dict(physical)
    if set(first_digest) != set(CURRENT_PROJECTION_TABLES):
        raise RC8PartitionedBuildError("Missing exported normalized table")
    replay_digest, replay_counts, _, _ = run_pass(
        work_root / "replay.sqlite3", replay=True, write=False
    )
    if digest != replay_digest or counts != replay_counts:
        raise RC8PartitionedBuildError("Independent replay digest or counts differ")
    guard("completed_before_manifests")
    for table, expected in first_digest.items():
        h = hashlib.sha256()
        with (candidate_dir / "normalized" / f"{table}.parquet").open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
        if h.hexdigest() != expected:
            raise RC8PartitionedBuildError("Candidate Parquet changed after export")

    capture_digest = hashlib.sha256(
        json.dumps(
            recovered_manifest["content_sha256"], sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    provenance = {
        "schema_version": 1,
        "status": "CANDIDATE_DATASET_NOT_SOURCE_QUALIFIED",
        "policy": "RC8_CURRENT_OVERLAY_V1",
        "engine": "RC8_DISK_BACKED_V1",
        "account_id": account_id,
        "projection_id": projection_id,
        "source_plan_id": plan["candidate_id"],
        "baseline_run_id": str(baseline_run_id),
        "delta_run_ids": [str(x) for x in delta_run_ids],
        "recovery_run_id": str(recovery_run_id),
        "recovery_content_digest": capture_digest,
        "source_history_id": plan["source_history_id"],
        "source_id_set_sha256": source_doc["id_set_sha256"],
        "exception_ledger_sha256": plan["exception_ledger_sha256"],
        "logical_digest": digest, "replay_digest": replay_digest,
        "deterministic_replay_equal": True,
        "parquet_sha256": first_digest,
        "row_counts": counts,
        "diagnostics": diagnostics | substitutions,
        "resource_headroom_gib": initial_resources,
        "peak_rss_gib": peak_rss,
        "resource_checkpoints": checkpoints,
        "source_labels_threads_mime_verified": False,
        "independent_incremental_equals_rebuild": False,
        "current_pointer_promoted": False,
        "stable_release_qualified": False,
    }
    # Atomic create-only manifests are last: a partial export can never be READY.
    write_private_inventory(candidate_dir / "rc8-provenance.json", provenance)
    write_private_inventory(candidate_dir / "projection-data.json", {
        "projection_schema_version": "1",
        "normalized_schema_version": NORMALIZED_SCHEMA_VERSION,
        "account_id": account_id, "projection_id": projection_id,
        "tables": list(CURRENT_PROJECTION_TABLES),
        "row_counts": counts, "logical_digest": digest,
        "status": "CANDIDATE",
    })
    if control.load_current(account_id) is not None:
        raise RC8PartitionedBuildError("Current pointer modified during build")
    # The two scratch databases are new, transient private state. Remove them
    # only after both immutable candidate manifests are fully persisted.
    shutil.rmtree(work_root)
    return {
        "status": "CANDIDATE_DATASET_NOT_SOURCE_QUALIFIED",
        "projection_id": projection_id,
        "source_plan_id": plan["candidate_id"],
        "source_history_id": plan["source_history_id"],
        "source_message_count": len(source_ids),
        "row_counts": counts, "logical_digest": digest,
        "replay_digest": replay_digest, "deterministic_replay_equal": True,
        "peak_rss_gib": peak_rss,
        "diagnostics": diagnostics | substitutions,
        "current_pointer_promoted": False, "stable_release_qualified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--projection-root", type=Path, required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--baseline-run-id", type=UUID, required=True)
    parser.add_argument("--delta-run-id", type=UUID, action="append", required=True)
    parser.add_argument("--source-inventory", type=Path, required=True)
    parser.add_argument("--recovery-run-id", type=UUID, required=True)
    parser.add_argument("--min-free-gib", type=int, default=40)
    parser.add_argument("--min-available-ram-gib", type=int, default=24)
    parser.add_argument("--batch-size", type=int, default=2048)
    args = parser.parse_args()
    try:
        result = build_partitioned_candidate(
            artifact_root=args.artifact_root, projection_root=args.projection_root,
            account_id=args.account_id, baseline_run_id=args.baseline_run_id,
            delta_run_ids=tuple(args.delta_run_id), source_inventory=args.source_inventory,
            recovery_run_id=args.recovery_run_id,
            min_free_gib=args.min_free_gib,
            min_available_ram_gib=args.min_available_ram_gib,
            batch_size=args.batch_size,
        )
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        print("RC8 PARTITIONED MATERIALIZATION FAILED: exception_type=" + type(exc).__name__)
        print("No Current promotion. Preserve staged files for investigation.")
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
