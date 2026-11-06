#!/usr/bin/env python3
"""Read-only provenance audit for Gmail History MESSAGE_ADDED events without RAW.

This command reads existing local manifests, processed History events and only the
baseline message IDs. It makes no Gmail API calls, writes no artifacts and never
prints message IDs, header values, bodies, email addresses, or OAuth secrets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

import pyarrow.parquet as pq
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.domain import GmailHistoryEvent, GmailHistoryEventType
from gmail_ingestion.persistence import LocalProjectionBatchReader, RawMessageWriter
from gmail_ingestion.projection import validate_delta_chain


@dataclass(frozen=True, slots=True)
class MissingAddition:
    """One processed MESSAGE_ADDED event whose own run lacks validated RAW."""

    event: GmailHistoryEvent
    later_deleted: bool


def classify_missing_addition(
    *,
    later_deleted: bool,
    in_baseline: bool,
    other_run_raw: bool,
) -> str:
    """Classify provenance conservatively without inferring missing message bodies."""
    if later_deleted:
        return "later_deleted"
    if other_run_raw:
        return "raw_in_other_run"
    if in_baseline:
        return "baseline_present_needs_review"
    return "unresolved_missing"


def audit(
    *,
    artifact_root: Path,
    account_id: str,
    baseline_run_id: UUID,
    delta_run_ids: tuple[UUID, ...],
    example_limit: int = 5,
) -> dict[str, object]:
    """Inspect only committed local evidence and return content-free metrics."""
    if not delta_run_ids:
        raise ValueError("At least one --delta-run-id is required")
    if example_limit < 0:
        raise ValueError("example_limit must be non-negative")
    store = LocalArtifactStore(artifact_root)
    root = store.run_root("gmail.projection_baseline", baseline_run_id)
    manifest_path = root / "baseline_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("status") != "COMPLETED"
        or manifest.get("source_snapshot_run_id") != str(baseline_run_id)
        or manifest.get("account_id") != account_id
    ):
        raise ValueError("Baseline completion/identity evidence is invalid")

    batches = LocalProjectionBatchReader(store).read_many(delta_run_ids)
    events = validate_delta_chain(
        account_id=account_id,
        start_history_id=str(manifest["history_id"]),
        target_history_id=batches[-1].output_history_id,
        batches=batches,
    )
    ordered = sorted(
        events,
        key=lambda event: (
            int(event.history_id), event.event_ordinal, event.event_id
        ),
    )
    raw_writer = RawMessageWriter(store)
    seen_later_deletes: set[str] = set()
    missing: list[MissingAddition] = []
    additions_total = 0
    for event in reversed(ordered):
        if event.event_type is GmailHistoryEventType.MESSAGE_DELETED:
            seen_later_deletes.add(event.message_id)
        elif event.event_type is GmailHistoryEventType.MESSAGE_ADDED:
            additions_total += 1
            if not raw_writer.exists(event.run_id, event.message_id):
                missing.append(
                    MissingAddition(
                        event=event,
                        later_deleted=event.message_id in seen_later_deletes,
                    )
                )

    missing.reverse()
    missing_ids = {entry.event.message_id for entry in missing}
    baseline_ids = _baseline_matches(root, manifest, missing_ids) if missing else set()
    source_run_ids = (baseline_run_id, *delta_run_ids)
    classifications: Counter[str] = Counter()
    missing_by_run: Counter[str] = Counter()
    examples: list[dict[str, str]] = []
    for entry in missing:
        event = entry.event
        other_raw = any(
            raw_writer.exists(run_id, event.message_id)
            for run_id in source_run_ids
            if run_id != event.run_id
        )
        classification = classify_missing_addition(
            later_deleted=entry.later_deleted,
            in_baseline=event.message_id in baseline_ids,
            other_run_raw=other_raw,
        )
        classifications[classification] += 1
        missing_by_run[str(event.run_id)] += 1
        if len(examples) < example_limit:
            fingerprint = hashlib.sha256(
                f"{event.run_id}:{event.message_id}".encode("utf-8")
            ).hexdigest()[:16]
            examples.append(
                {
                    "event_fingerprint": fingerprint,
                    "run_id": str(event.run_id),
                    "class": classification,
                }
            )

    return {
        "account_fingerprint": hashlib.sha256(
            account_id.encode("utf-8")
        ).hexdigest()[:16],
        "baseline_run_id": str(baseline_run_id),
        "baseline_history_id": str(manifest["history_id"]),
        "target_history_id": batches[-1].output_history_id,
        "total_events": len(ordered),
        "message_add_events": additions_total,
        "missing_addition_raw_count": len(missing),
        "missing_by_run": dict(sorted(missing_by_run.items())),
        "classifications": dict(sorted(classifications.items())),
        "examples": examples,
        "gmail_api_calls": 0,
        "persistent_writes": 0,
        "scope_note": (
            "A baseline match or alternative RAW is evidence of potential recovery, "
            "not permission to skip the original event or invent missing content."
        ),
    }


def _baseline_matches(
    root: Path,
    manifest: dict[str, object],
    wanted: set[str],
) -> set[str]:
    """Read only message_id Parquet columns, never mail bodies or headers."""
    found: set[str] = set()
    if manifest.get("layout") == "segmented-parquet-v1":
        segment_count = manifest.get("segment_count")
        if not isinstance(segment_count, int) or segment_count < 1:
            raise ValueError("Invalid segmented baseline segment count")
        paths = [
            root / "segments" / f"{index:08d}" / "messages.parquet"
            for index in range(segment_count)
        ]
    else:
        paths = [root / "normalized" / "messages.parquet"]
    for path in paths:
        table = pq.read_table(path, columns=["message_id"])
        found.update(
            message_id for message_id in table.column("message_id").to_pylist()
            if isinstance(message_id, str) and message_id in wanted
        )
        if found == wanted:
            break
    return found


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit missing Gmail History addition RAW without contacting Gmail."
    )
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--baseline-run-id", required=True, type=UUID)
    parser.add_argument("--delta-run-id", required=True, action="append", type=UUID)
    parser.add_argument("--example-limit", type=int, default=5)
    args = parser.parse_args()

    try:
        result = audit(
            artifact_root=args.artifact_root,
            account_id=args.account_id,
            baseline_run_id=args.baseline_run_id,
            delta_run_ids=tuple(args.delta_run_id),
            example_limit=args.example_limit,
        )
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(f"AUDIT FAILED: exception_type={type(exc).__name__}")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
