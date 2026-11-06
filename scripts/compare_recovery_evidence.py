#!/usr/bin/env python3
"""Compare two dated Gmail recovery captures without Gmail API calls or writes.

Report only aggregate data-quality metrics. This cannot prove historical state.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import UUID

from probe_missing_added_raw_live import unresolved_ids
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.persistence.raw_message_writer import RawMessageWriter

_JOB = "gmail.recovery_evidence"


class RecoveryComparisonError(ValueError):
    """One capture is incomplete, inconsistent or incompatible."""


def _manifest(
    store: LocalArtifactStore,
    run_id: UUID,
    *,
    account_id: str,
    baseline_run_id: UUID,
    delta_run_ids: tuple[UUID, ...],
    expected_ids: set[str],
    target_history_id: str,
) -> tuple[set[str], set[str], dict[str, Any]]:
    path = store.run_root(_JOB, run_id) / "recovery_manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RecoveryComparisonError("Missing or invalid recovery completion evidence") from exc
    if not isinstance(payload, dict):
        raise RecoveryComparisonError("Recovery manifest must be an object")
    expected = {
        "schema_version": 1,
        "status": "COMPLETE",
        "namespace": _JOB,
        "non_authoritative": True,
        "account_id": account_id,
        "baseline_run_id": str(baseline_run_id),
        "delta_run_ids": [str(x) for x in delta_run_ids],
        "recovery_run_id": str(run_id),
        "historical_target_history_id": target_history_id,
        "unique_ids_evaluated": len(expected_ids),
    }
    if any(payload.get(key) != value for key, value in expected.items()):
        raise RecoveryComparisonError("Recovery completion identity/frontier mismatch")
    captured = payload.get("captured_ids")
    not_found = payload.get("not_found_ids")
    if (
        not isinstance(captured, list) or not isinstance(not_found, list)
        or not all(isinstance(x, str) for x in captured + not_found)
        or len(set(captured)) != len(captured)
        or len(set(not_found)) != len(not_found)
        or set(captured).intersection(not_found)
        or set(captured + not_found) != expected_ids
        or payload.get("captured_current_full") != len(captured)
        or payload.get("not_found_404_now") != len(not_found)
    ):
        raise RecoveryComparisonError("Recovery manifest captured/not-found inventory mismatch")
    return set(captured), set(not_found), payload


def _logical_bytes(payload: dict[str, Any] | Any) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def _history_bucket(payload: dict[str, Any] | Any, target: str) -> str:
    value = payload.get("historyId")
    if isinstance(value, str) and value.isdecimal():
        return "gt_target" if int(value) > int(target) else "le_target"
    return "unknown"


def compare(
    *,
    artifact_root: Path,
    account_id: str,
    baseline_run_id: UUID,
    delta_run_ids: tuple[UUID, ...],
    first_run_id: UUID,
    second_run_id: UUID,
) -> dict[str, object]:
    """Check inventories and message JSON equality without exposing Gmail identifiers."""
    if first_run_id == second_run_id:
        raise RecoveryComparisonError("Use two distinct recovery run IDs")
    ids, unresolved_events, target = unresolved_ids(
        artifact_root, account_id, baseline_run_id, delta_run_ids
    )
    store = LocalArtifactStore(artifact_root)
    first, first_404, first_meta = _manifest(
        store, first_run_id, account_id=account_id,
        baseline_run_id=baseline_run_id, delta_run_ids=delta_run_ids,
        expected_ids=ids, target_history_id=target,
    )
    second, second_404, second_meta = _manifest(
        store, second_run_id, account_id=account_id,
        baseline_run_id=baseline_run_id, delta_run_ids=delta_run_ids,
        expected_ids=ids, target_history_id=target,
    )
    writer = RawMessageWriter(store, job_id=_JOB)
    identical = different = 0
    label_sets_differ = histories_differ = 0
    bucket_counts: Counter[str] = Counter()
    for message_id in sorted(first.intersection(second)):
        try:
            old = writer.read(first_run_id, message_id)
            new = writer.read(second_run_id, message_id)
        except (OSError, ValueError) as exc:
            raise RecoveryComparisonError(
                "Captured message evidence is missing or invalid"
            ) from exc
        if hashlib.sha256(_logical_bytes(old)).digest() == hashlib.sha256(
            _logical_bytes(new)
        ).digest():
            identical += 1
        else:
            different += 1
        if set(old.get("labelIds") or []) != set(new.get("labelIds") or []):
            label_sets_differ += 1
        if old.get("historyId") != new.get("historyId"):
            histories_differ += 1
        bucket_counts[_history_bucket(old, target)] += 1
    only_first = first - second
    only_second = second - first
    same_missing_ids = first_404 == second_404
    stable = (
        not only_first and not only_second and same_missing_ids and different == 0
    )
    return {
        "status": "COMPARED",
        "historical_target_history_id": target,
        "historical_unresolved_add_events": unresolved_events,
        "distinct_recovery_runs": 2,
        "first_observed_at_utc": first_meta.get("observed_at_utc"),
        "second_observed_at_utc": second_meta.get("observed_at_utc"),
        "first_captured": len(first),
        "second_captured": len(second),
        "first_not_found_404": len(first_404),
        "second_not_found_404": len(second_404),
        "not_found_sets_match": same_missing_ids,
        "captured_only_first": len(only_first),
        "captured_only_second": len(only_second),
        "matched_captured": len(first & second),
        "identical_full_json": identical,
        "changed_full_json": different,
        "changed_label_sets": label_sets_differ,
        "changed_message_history_ids": histories_differ,
        "matched_history_id_le_target": bucket_counts["le_target"],
        "matched_history_id_gt_target": bucket_counts["gt_target"],
        "matched_history_id_unknown": bucket_counts["unknown"],
        "two_capture_payloads_stable": stable,
        "historical_projection_qualified": False,
        "gmail_api_calls": 0,
        "persistent_writes": 0,
        "warning": (
            "Even matching later captures cannot reconstruct missing historical "
            "RAW or prove label state at the past target checkpoint."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--baseline-run-id", type=UUID, required=True)
    parser.add_argument("--delta-run-id", type=UUID, action="append", required=True)
    parser.add_argument("--first-recovery-run-id", type=UUID, required=True)
    parser.add_argument("--second-recovery-run-id", type=UUID, required=True)
    args = parser.parse_args()
    try:
        result = compare(
            artifact_root=args.artifact_root,
            account_id=args.account_id,
            baseline_run_id=args.baseline_run_id,
            delta_run_ids=tuple(args.delta_run_id),
            first_run_id=args.first_recovery_run_id,
            second_run_id=args.second_recovery_run_id,
        )
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(f"RECOVERY COMPARISON FAILED: exception_type={type(exc).__name__}")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
