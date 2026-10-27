#!/usr/bin/env python3
"""Bounded real-artifact qualification for Gmail Ingestion 0.5.0 projection."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from uuid import UUID

from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion import __version__
from gmail_ingestion.application import ProjectCurrentState
from gmail_ingestion.domain import GmailHistoryEventType
from gmail_ingestion.persistence import LocalProjectionBatchReader, LocalProjectionStore

_ALLOWED_VERSIONS = frozenset({"0.5.0rc4", "0.5.0"})
_REQUIRED_EVENTS = frozenset(GmailHistoryEventType)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Qualify projection against real committed Gmail artifacts without making Gmail API "
            "calls or mutating the mailbox."
        )
    )
    parser.add_argument("--account-id", required=True, help="Opaque Gmail account ID.")
    parser.add_argument("--baseline-run-id", required=True, help="Qualified baseline run UUID.")
    parser.add_argument(
        "--delta-run-id",
        action="append",
        required=True,
        help="Committed incremental run UUID in baseline-to-target order. Repeat as needed.",
    )
    parser.add_argument(
        "--baseline-dataset-version",
        help="Baseline dataset identity. Defaults to the baseline run UUID.",
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path(".gmail-ingestion"),
        help="PyIngestKit run-artifact root containing the real Gmail qualification artifacts.",
    )
    parser.add_argument(
        "--projection-root",
        type=Path,
        default=Path(".gmail-ingestion-projection-qualification"),
        help="Dedicated projection root for qualification.",
    )
    parser.add_argument(
        "--evidence-out",
        type=Path,
        required=True,
        help="Path for redacted JSON qualification evidence.",
    )
    args = parser.parse_args()

    if __version__ not in _ALLOWED_VERSIONS:
        raise SystemExit(
            "Projection stable qualification requires gmail-ingestion "
            f"{sorted(_ALLOWED_VERSIONS)}, found {__version__}"
        )

    baseline_run_id = UUID(args.baseline_run_id)
    delta_run_ids = tuple(UUID(value) for value in args.delta_run_id)
    artifact_store = LocalArtifactStore(args.artifact_root)
    projection_store = LocalProjectionStore(args.projection_root)
    batch_reader = LocalProjectionBatchReader(artifact_store)
    batches = batch_reader.read_many(delta_run_ids)

    event_counts = Counter(
        event.event_type.value
        for batch in batches
        for event in batch.events
    )
    missing = sorted(
        event_type.value
        for event_type in _REQUIRED_EVENTS
        if event_counts[event_type.value] < 1
    )
    if missing:
        raise SystemExit(
            "Live projection gate requires all four qualified event classes; missing: "
            + ", ".join(missing)
        )

    use_case = ProjectCurrentState(
        artifact_store,
        projection_store,
        batch_reader=batch_reader,
    )
    result = use_case.run(
        account_id=args.account_id,
        baseline_run_id=baseline_run_id,
        baseline_dataset_version=(
            args.baseline_dataset_version or str(baseline_run_id)
        ),
        delta_run_ids=delta_run_ids,
    )
    if result.logical_digest != result.rebuild_digest:
        raise SystemExit("Projection digest mismatch after live-artifact materialization")
    if not result.promoted:
        raise SystemExit("Projection live gate expected an atomic Current promotion")

    replay = use_case.run(
        account_id=args.account_id,
        baseline_run_id=baseline_run_id,
        baseline_dataset_version=(
            args.baseline_dataset_version or str(baseline_run_id)
        ),
        delta_run_ids=delta_run_ids,
    )
    if not replay.already_current:
        raise SystemExit("Projection replay did not recognize the already-current frontier")
    if replay.logical_digest != result.logical_digest:
        raise SystemExit("Projection replay changed the logical digest")

    current = projection_store.load_current(args.account_id)
    if current is None or current.projection_id != result.projection_id:
        raise SystemExit("Current projection pointer does not reference the qualified version")
    version = projection_store.load_version(args.account_id, result.projection_id)
    if version is None or version.state.logical_digest != result.logical_digest:
        raise SystemExit("Immutable projection version evidence is missing or inconsistent")

    evidence = {
        "evidence_schema_version": 1,
        "connector_version": __version__,
        "qualification": "gmail_projection_0_5_live_artifacts",
        "status": "PASS",
        "account_fingerprint": hashlib.sha256(
            args.account_id.encode("utf-8")
        ).hexdigest()[:16],
        "baseline_run_id": str(baseline_run_id),
        "delta_run_ids": [str(run_id) for run_id in delta_run_ids],
        "event_counts": dict(sorted(event_counts.items())),
        "all_four_event_classes": True,
        "projection_id": result.projection_id,
        "input_history_id": result.input_history_id,
        "target_history_id": result.target_history_id,
        "logical_digest": result.logical_digest,
        "rebuild_digest": result.rebuild_digest,
        "incremental_equals_rebuild": True,
        "incremental_batch_count": result.incremental_batch_count,
        "rebuild_batch_count": result.rebuild_batch_count,
        "atomic_current_pointer": True,
        "immutable_version_evidence": True,
        "replay_already_current": True,
        "gmail_api_calls_by_projection": 0,
        "oauth_scope_change": False,
        "contains_message_content": False,
        "contains_oauth_secrets": False,
    }
    args.evidence_out.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_out.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    print("Projection live-artifact qualification: PASS")
    print(f"projection_id: {result.projection_id}")
    print(f"target_history_id: {result.target_history_id}")
    print(f"logical_digest: {result.logical_digest}")
    print("event_counts:")
    for event_type, count in sorted(event_counts.items()):
        print(f"  {event_type}: {count}")
    print(f"evidence: {args.evidence_out}")


if __name__ == "__main__":
    main()
