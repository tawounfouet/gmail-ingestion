#!/usr/bin/env python3
"""Offline preflight for the RC7-to-RC8 Gmail Current reconciliation.

This tool uses committed local artifacts only. It does not contact Gmail, modify
RAW, advance checkpoints, or move a Current pointer. It deliberately cannot
issue a production GO without a separate source-validated inventory.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from uuid import UUID

from compare_recovery_evidence import _manifest
from probe_missing_added_raw_live import unresolved_ids
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.domain import GmailHistoryEvent, GmailHistoryEventType
from gmail_ingestion.persistence import LocalProjectionBatchReader, RawMessageWriter
from gmail_ingestion.projection import validate_delta_chain


def classify_missing_additions(
    events: Sequence[GmailHistoryEvent],
    raw_exists: Callable[[UUID, str], bool],
) -> dict[str, int]:
    """Classify missing event-local RAW using *only* later committed deletes.

    A later deletion can make an unavailable add irrelevant to the final state.
    Neither a 404 observed later nor a RAW captured in another run is sufficient.
    IDs and message contents are intentionally never returned.
    """
    ordered = sorted(
        events, key=lambda event: (int(event.history_id), event.event_ordinal, event.event_id)
    )
    later_deleted: set[str] = set()
    classes: Counter[str] = Counter()
    for event in reversed(ordered):
        if event.event_type is GmailHistoryEventType.MESSAGE_DELETED:
            later_deleted.add(event.message_id)
        elif event.event_type is GmailHistoryEventType.MESSAGE_ADDED:
            if not raw_exists(event.run_id, event.message_id):
                key = (
                    "superseded_by_committed_delete"
                    if event.message_id in later_deleted
                    else "unresolved_without_event_local_raw"
                )
                classes[key] += 1
    classes["total_missing_addition_events"] = (
        classes["superseded_by_committed_delete"]
        + classes["unresolved_without_event_local_raw"]
    )
    return dict(classes)


def preflight(
    *,
    artifact_root: Path,
    account_id: str,
    baseline_run_id: UUID,
    historical_delta_run_ids: tuple[UUID, ...],
    catchup_run_id: UUID,
    first_recovery_run_id: UUID,
    second_recovery_run_id: UUID,
    expected_checkpoint: str,
) -> dict[str, object]:
    """Verify completed evidence identities and publish only aggregate blockers."""
    if not historical_delta_run_ids or len(set(historical_delta_run_ids)) != len(
        historical_delta_run_ids
    ):
        raise ValueError("Historical deltas must be nonempty, unique, and ordered")
    all_run_ids = (baseline_run_id, *historical_delta_run_ids, catchup_run_id)
    if len(set(all_run_ids)) != len(all_run_ids):
        raise ValueError("Baseline and delta/catchup run IDs must be distinct")
    if first_recovery_run_id == second_recovery_run_id:
        raise ValueError("Recovery captures must be distinct")
    if not expected_checkpoint.isdecimal() or not account_id:
        raise ValueError("Expected checkpoint and account identity are required")

    store = LocalArtifactStore(artifact_root)
    baseline_path = (
        store.run_root("gmail.projection_baseline", baseline_run_id) / "baseline_manifest.json"
    )
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    if (
        not isinstance(baseline, dict)
        or baseline.get("status") != "COMPLETED"
        or baseline.get("source_snapshot_run_id") != str(baseline_run_id)
        or baseline.get("account_id") != account_id
        or not isinstance(baseline.get("history_id"), str)
        or not baseline["history_id"].isdecimal()
    ):
        raise ValueError("Invalid completed baseline provenance")

    reader = LocalProjectionBatchReader(store)
    historic_batches = reader.read_many(historical_delta_run_ids)
    if not historic_batches:
        raise ValueError("Missing historical delta chain")
    historical_target = historic_batches[-1].output_history_id
    validate_delta_chain(
        account_id=account_id,
        start_history_id=baseline["history_id"],
        target_history_id=historical_target,
        batches=historic_batches,
    )

    catchup_batches = reader.read_many((catchup_run_id,))
    catchup = catchup_batches[0]
    if catchup.input_history_id != historical_target:
        raise ValueError("Catchup run does not start at the frozen historical frontier")
    if catchup.output_history_id != expected_checkpoint:
        raise ValueError("Catchup committed checkpoint differs from expected value")
    events = validate_delta_chain(
        account_id=account_id,
        start_history_id=baseline["history_id"],
        target_history_id=expected_checkpoint,
        batches=(*historic_batches, catchup),
    )

    historical_unresolved, _, verified_target = unresolved_ids(
        artifact_root, account_id, baseline_run_id, historical_delta_run_ids
    )
    if verified_target != historical_target:
        raise ValueError("Historical gap inventory uses a different target frontier")
    captured_first, not_found_first, _ = _manifest(
        store,
        first_recovery_run_id,
        account_id=account_id,
        baseline_run_id=baseline_run_id,
        delta_run_ids=historical_delta_run_ids,
        expected_ids=historical_unresolved,
        target_history_id=historical_target,
    )
    captured_second, not_found_second, _ = _manifest(
        store,
        second_recovery_run_id,
        account_id=account_id,
        baseline_run_id=baseline_run_id,
        delta_run_ids=historical_delta_run_ids,
        expected_ids=historical_unresolved,
        target_history_id=historical_target,
    )
    if captured_first != captured_second or not_found_first != not_found_second:
        raise ValueError("Dated recovery inventories disagree")
    recovery_reader = RawMessageWriter(store, job_id="gmail.recovery_evidence")
    for message_id in captured_first:
        if not (
            recovery_reader.exists(first_recovery_run_id, message_id)
            and recovery_reader.exists(second_recovery_run_id, message_id)
        ):
            raise ValueError("Recovery manifest claims a missing RAW payload")

    original_raw = RawMessageWriter(store)
    missing = classify_missing_additions(events, original_raw.exists)
    catchup_missing = classify_missing_additions(catchup.events, original_raw.exists)

    return {
        "status": "NO_GO_SOURCE_VALIDATION_PENDING",
        "historical_strict_qualified": not historical_unresolved,
        "current_state_qualified": False,
        "stable_release_qualified": False,
        "baseline_history_id": baseline["history_id"],
        "historical_target_history_id": historical_target,
        "committed_catchup_history_id": expected_checkpoint,
        "catchup_run_id": str(catchup_run_id),
        "committed_history_chain_length": len(historic_batches) + 1,
        "committed_history_event_count": len(events),
        "historical_unresolved_message_ids_count": len(historical_unresolved),
        "dated_recovery_captured_count": len(captured_first),
        "dated_recovery_404_count": len(not_found_first),
        "all_frontiers_missing_raw_events": missing,
        "catchup_missing_raw_events": catchup_missing,
        "source_inventory_completed": False,
        "source_inventory_race_window_closed": False,
        "candidate_current_state_built": False,
        "current_pointer_promoted": False,
        "gmail_api_calls": 0,
        "persistent_writes": 0,
        "caveat": (
            "Committed ingestion is not proof of projected-source equality. "
            "Recovery captures are dated, non-authoritative observations. "
            "The independent Gmail inventory, label/thread comparisons and "
            "inventory-window History catchup remain mandatory for a release GO."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline, content-free preflight of Gmail 0.5.0 Current reconciliation."
    )
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--baseline-run-id", type=UUID, required=True)
    parser.add_argument("--delta-run-id", action="append", type=UUID, required=True)
    parser.add_argument("--catchup-run-id", type=UUID, required=True)
    parser.add_argument("--first-recovery-run-id", type=UUID, required=True)
    parser.add_argument("--second-recovery-run-id", type=UUID, required=True)
    parser.add_argument("--expected-checkpoint", required=True)
    args = parser.parse_args()
    try:
        report = preflight(
            artifact_root=args.artifact_root,
            account_id=args.account_id,
            baseline_run_id=args.baseline_run_id,
            historical_delta_run_ids=tuple(args.delta_run_id),
            catchup_run_id=args.catchup_run_id,
            first_recovery_run_id=args.first_recovery_run_id,
            second_recovery_run_id=args.second_recovery_run_id,
            expected_checkpoint=args.expected_checkpoint,
        )
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print("PREFLIGHT FAILED: exception_type=" + type(exc).__name__)
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
