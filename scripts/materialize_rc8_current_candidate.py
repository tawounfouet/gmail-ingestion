#!/usr/bin/env python3
"""Materialize an isolated RC8 Current candidate from frozen, proven source IDs.

Explicitly not an historical reconstruction or a release qualification.
All legacy source RAW and checkpoints are immutable. Candidate data is written
only to a dedicated empty RC8 root after a strict disk-headroom check.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import shutil
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import psutil
from observe_current_source_inventory import write_private_inventory
from plan_rc8_current_candidate import _source, build_candidate_plan
from pyingestkit.artifacts.filesystem import LocalArtifactStore
from pyingestkit.core.exceptions import StorageError

from gmail_ingestion import __version__
from gmail_ingestion.application.rc8_current_reconciliation import (
    reconcile_current_rows,
)
from gmail_ingestion.domain import GmailHistoryEventType
from gmail_ingestion.normalization import normalize_snapshot
from gmail_ingestion.persistence import (
    LocalProjectionBatchReader,
    LocalProjectionStore,
    RawMessageWriter,
)
from gmail_ingestion.persistence.projection_dataset_store import (
    LocalProjectionDatasetStore,
    NormalizedBaselineReader,
)
from gmail_ingestion.projection import validate_delta_chain
from gmail_ingestion.projection.digest import logical_projection_digest
from gmail_ingestion.projection.message_delta import (
    CURRENT_PROJECTION_TABLES,
    MESSAGE_PROJECTION_TABLES,
    PreparedMessageDelta,
)


class CandidateMaterializationError(RuntimeError):
    """One or more RC8 source/dataset provenance guards failed."""


def require_resource_headroom(
    disk_root: Path, *, min_free_gib: int = 20, min_available_ram_gib: int = 24,
) -> dict[str, float]:
    """Fail before loading multi-million-row normalized baseline tables."""
    if min_free_gib < 1 or min_available_ram_gib < 1:
        raise CandidateMaterializationError("Positive memory and disk reserves required")
    free_disk = shutil.disk_usage(disk_root).free / 1024**3
    available_ram = psutil.virtual_memory().available / 1024**3
    if free_disk < min_free_gib:
        raise CandidateMaterializationError("Insufficient free disk for Current candidate")
    if available_ram < min_available_ram_gib:
        raise CandidateMaterializationError("Insufficient available RAM for Current candidate")
    return {
        "free_disk_gib": round(free_disk, 2),
        "available_ram_gib": round(available_ram, 2),
    }


def _source_manifest(
    store: LocalArtifactStore,
    run_id: UUID,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    if run_id.int == 0:
        raise CandidateMaterializationError("Recovery run UUID cannot be nil")
    root = store.run_root("gmail.rc8_source_evidence", run_id)
    manifest = json.loads((root / "source_manifest.json").read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise CandidateMaterializationError("Recovery manifest must be an object")
    required = plan.get("private_recovery_required_ids")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("status") != "COMPLETED"
        or manifest.get("job_id") != "gmail.rc8_source_evidence"
        or manifest.get("run_id") != str(run_id)
        or manifest.get("candidate_id") != plan.get("candidate_id")
        or manifest.get("account_id") != plan.get("account_id")
        or manifest.get("source_history_id") != plan.get("source_history_id")
        or manifest.get("source_id_set_sha256") != plan.get("source_id_set_sha256")
        or manifest.get("exception_ledger_sha256") != plan.get("exception_ledger_sha256")
        or manifest.get("recovery_message_ids") != required
        or manifest.get("recovery_message_count") != len(required)
    ):
        raise CandidateMaterializationError("Current recovery evidence identity mismatch")
    digests = manifest.get("content_sha256")
    if not isinstance(digests, dict) or set(digests) != set(required):
        raise CandidateMaterializationError("Current recovery content digest ledger invalid")
    return manifest


def _raw_recovery(
    store: LocalArtifactStore,
    run_id: UUID,
    manifest: Mapping[str, Any],
) -> dict[str, Mapping[str, Any]]:
    reader = RawMessageWriter(store, job_id="gmail.rc8_source_evidence")
    result: dict[str, Mapping[str, Any]] = {}
    for mid in manifest["recovery_message_ids"]:
        payload = reader.read(run_id, mid)
        digest = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        if digest != manifest["content_sha256"][mid]:
            raise CandidateMaterializationError("Recovery RAW content digest mismatch")
        history_id = payload.get("historyId")
        if (
            not isinstance(history_id, str)
            or not history_id.isdecimal()
            or int(history_id) > int(manifest["source_history_id"])
        ):
            raise CandidateMaterializationError(
                "Recovery message content postdates the observed source frontier"
            )
        result[mid] = payload
    return result


def _prepared_events(
    *,
    account: Any,
    plan: Mapping[str, Any],
    original_raw: RawMessageWriter,
    recovery: Mapping[str, Mapping[str, Any]],
    events: list[Any],
    source_ids: set[str],
    projection_id: str,
) -> tuple[list[PreparedMessageDelta], dict[str, int]]:
    prepared: list[PreparedMessageDelta] = []
    substituted = omitted = 0
    for event in sorted(events, key=lambda e: (int(e.history_id), e.event_ordinal, e.event_id)):
        if event.event_type is not GmailHistoryEventType.MESSAGE_ADDED:
            prepared.append(PreparedMessageDelta(event=event))
            continue
        if event.message_id not in source_ids:
            # Current scope exclusion is NOT a forged deletion or proof about
            # the historical state at the event's historyId.
            omitted += 1
            continue
        try:
            payload = original_raw.read(event.run_id, event.message_id)
        except (StorageError, OSError, ValueError):
            payload = recovery.get(event.message_id)
            if payload is None:
                raise CandidateMaterializationError(
                    "Current in-scope added message has no proven original/current RAW"
                ) from None
            substituted += 1
        normalized = normalize_snapshot(
            account, (), (payload,),
            ingested_at=event.observed_at,
            dataset_version=projection_id,
            source_connector_version=__version__,
        )
        prepared.append(PreparedMessageDelta(
            event=event,
            normalized_rows={
                name: normalized.rows(name)
                for name in MESSAGE_PROJECTION_TABLES
            },
        ))
    return prepared, {
        "current_only_raw_substitutions": substituted,
        "out_of_scope_adds_omitted": omitted,
    }


def build_current_candidate(
    *,
    artifact_root: Path,
    projection_root: Path,
    account_id: str,
    baseline_run_id: UUID,
    delta_run_ids: tuple[UUID, ...],
    source_inventory: Path,
    recovery_run_id: UUID,
    min_free_gib: int = 20,
    min_available_ram_gib: int = 24,
) -> dict[str, object]:
    """Create a separately versioned source-overlay dataset without promotion."""
    if min_free_gib < 1:
        raise CandidateMaterializationError("Positive minimum free disk is required")
    artifact_root = artifact_root.resolve()
    projection_root = projection_root.resolve()
    if projection_root == artifact_root or artifact_root in projection_root.parents:
        raise CandidateMaterializationError("Candidate projection root must be isolated")
    if projection_root.name in {".gmail-ingestion-control", ".gmail-ingestion-control-live-0.5.0"}:
        raise CandidateMaterializationError("Refusing an authoritative control-plane root")
    output_filesystem = (
        projection_root if projection_root.is_dir() else projection_root.parent
    )
    if not output_filesystem.is_dir():
        raise CandidateMaterializationError("Candidate output parent does not exist")
    headroom = require_resource_headroom(
        output_filesystem,
        min_free_gib=min_free_gib,
        min_available_ram_gib=min_available_ram_gib,
    )
    source_ids, source_doc = _source(source_inventory)
    plan = build_candidate_plan(
        artifact_root=artifact_root, account_id=account_id,
        baseline_run_id=baseline_run_id, delta_run_ids=delta_run_ids,
        source_inventory=source_inventory, min_free_gib=min_free_gib,
    )
    if plan["source_id_set_sha256"] != source_doc["id_set_sha256"]:
        raise CandidateMaterializationError("Source identity changed during planning")
    store = LocalArtifactStore(artifact_root)
    manifest = _source_manifest(store, recovery_run_id, plan)
    recovery = _raw_recovery(store, recovery_run_id, manifest)
    capture_digest = hashlib.sha256(
        json.dumps(
            manifest["content_sha256"], sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    identity = {
        "policy": "RC8_CURRENT_OVERLAY_V1",
        "plan_id": plan["candidate_id"],
        "source_history_id": plan["source_history_id"],
        "source_id_set_sha256": plan["source_id_set_sha256"],
        "recovery_run_id": str(recovery_run_id),
        "recovery_content_digest": capture_digest,
    }
    projection_id = "proj_" + hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:32]
    projection_store = LocalProjectionStore(projection_root)
    if projection_store.load_current(account_id) is not None:
        raise CandidateMaterializationError(
            "The isolated RC8 root has an authoritative Current pointer"
        )
    dataset_store = LocalProjectionDatasetStore(projection_store)
    if dataset_store.read_candidate_manifest(account_id, projection_id) is not None:
        raise CandidateMaterializationError(
            "Existing immutable Current candidate must not be overwritten"
        )
    candidate_data_dir = projection_store.candidate_data_dir(account_id, projection_id)
    if any(candidate_data_dir.iterdir()):
        raise CandidateMaterializationError("Incomplete candidate data already exists")

    reader = NormalizedBaselineReader(store)
    baseline = reader.read(baseline_run_id, account_id)
    batches = LocalProjectionBatchReader(store).read_many(delta_run_ids)
    all_events = validate_delta_chain(
        account_id=account_id,
        start_history_id=str(baseline.account.history_id),
        target_history_id=batches[-1].output_history_id,
        batches=batches,
    )
    events = [
        event for event in all_events
        if int(event.history_id) <= int(plan["source_history_id"])
    ]
    normalized_current: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for mid, payload in recovery.items():
        normalized = normalize_snapshot(
            baseline.account, (), (payload,),
            ingested_at=datetime(1970, 1, 1, tzinfo=UTC),
            dataset_version=projection_id,
            source_connector_version=__version__,
        )
        normalized_current[mid] = {
            name: normalized.rows(name) for name in MESSAGE_PROJECTION_TABLES
        }
    prepared, substitutions = _prepared_events(
        account=baseline.account, plan=plan,
        original_raw=RawMessageWriter(store),
        recovery=recovery,
        events=events,
        source_ids=source_ids,
        projection_id=projection_id,
    )
    rows, diagnostics = reconcile_current_rows(
        account_id=account_id,
        baseline_tables=baseline.tables,
        prepared_deltas=prepared,
        source_message_ids=source_ids,
        current_recovery_rows=normalized_current,
        committed_events=events,
    )
    digest = logical_projection_digest(
        rows, table_names=CURRENT_PROJECTION_TABLES
    )
    # Recompute from the same immutable baseline and frozen event set to check
    # deterministic replay. This is not independent source validation.
    require_resource_headroom(
        output_filesystem, min_free_gib=min_free_gib,
        min_available_ram_gib=min_available_ram_gib,
    )
    replay_rows, _ = reconcile_current_rows(
        account_id=account_id,
        baseline_tables=baseline.tables,
        prepared_deltas=tuple(reversed(prepared)),
        source_message_ids=source_ids,
        current_recovery_rows=normalized_current,
        committed_events=events,
    )
    replay_digest = logical_projection_digest(
        replay_rows, table_names=CURRENT_PROJECTION_TABLES
    )
    if replay_digest != digest:
        raise CandidateMaterializationError("RC8 deterministic replay digest differs")
    del replay_rows
    gc.collect()

    require_resource_headroom(
        output_filesystem, min_free_gib=min_free_gib,
        min_available_ram_gib=min_available_ram_gib,
    )
    result = dataset_store.write_candidate(
        account_id, projection_id, rows,
        logical_digest=digest, table_names=CURRENT_PROJECTION_TABLES,
    )
    physical_digests: dict[str, str] = {}
    for name, path in result.table_paths.items():
        checksum = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(chunk)
        physical_digests[name] = checksum.hexdigest()
    evidence = {
        "schema_version": 1,
        "status": "CANDIDATE_DATASET_NOT_SOURCE_QUALIFIED",
        "policy": "RC8_CURRENT_OVERLAY_V1",
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
        "logical_digest": digest,
        "parquet_sha256": dict(sorted(physical_digests.items())),
        "replay_digest": replay_digest,
        "deterministic_replay_equal": True,
        "row_counts": dict(result.row_counts),
        "resource_headroom_gib": headroom,
        "diagnostics": dict(diagnostics | substitutions),
        "source_labels_threads_mime_verified": False,
        "independent_incremental_equals_rebuild": False,
        "current_pointer_promoted": False,
        "stable_release_qualified": False,
    }
    write_private_inventory(candidate_data_dir / "rc8-provenance.json", evidence)
    if projection_store.load_current(account_id) is not None:
        raise CandidateMaterializationError("Current pointer changed during candidate creation")
    return {
        "status": evidence["status"],
        "projection_id": projection_id,
        "source_plan_id": plan["candidate_id"],
        "source_history_id": plan["source_history_id"],
        "source_message_count": len(source_ids),
        "logical_digest": digest,
        "replay_digest": replay_digest,
        "deterministic_replay_equal": True,
        "row_counts": dict(result.row_counts),
        "diagnostics": dict(diagnostics | substitutions),
        "current_pointer_promoted": False,
        "stable_release_qualified": False,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--artifact-root", type=Path, required=True)
    p.add_argument("--projection-root", type=Path, required=True)
    p.add_argument("--account-id", required=True)
    p.add_argument("--baseline-run-id", type=UUID, required=True)
    p.add_argument("--delta-run-id", type=UUID, action="append", required=True)
    p.add_argument("--source-inventory", type=Path, required=True)
    p.add_argument("--recovery-run-id", type=UUID, required=True)
    p.add_argument("--min-free-gib", type=int, default=20)
    p.add_argument("--min-available-ram-gib", type=int, default=24)
    args = p.parse_args()
    try:
        report = build_current_candidate(
            artifact_root=args.artifact_root, projection_root=args.projection_root,
            account_id=args.account_id, baseline_run_id=args.baseline_run_id,
            delta_run_ids=tuple(args.delta_run_id), source_inventory=args.source_inventory,
            recovery_run_id=args.recovery_run_id, min_free_gib=args.min_free_gib,
            min_available_ram_gib=args.min_available_ram_gib,
        )
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        print("RC8 MATERIALIZATION FAILED: exception_type=" + type(exc).__name__)
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
