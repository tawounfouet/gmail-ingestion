#!/usr/bin/env python3
"""Check normalized semantic parity across two dated Gmail recovery captures.

Strictly offline and read-only. Never print raw message IDs, email addresses,
headers, bodies, decoded content, per-message hashes or OAuth material.
This is diagnostic evidence, NOT historical source reconstruction or approval.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from compare_recovery_evidence import _manifest
from probe_missing_added_raw_live import unresolved_ids
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.domain import GmailAccount
from gmail_ingestion.normalization import normalize_snapshot
from gmail_ingestion.persistence.raw_message_writer import RawMessageWriter
from gmail_ingestion.projection.digest import logical_projection_digest
from gmail_ingestion.projection.message_delta import CURRENT_PROJECTION_TABLES

_JOB = "gmail.recovery_evidence"
_FIXED_TIME = datetime(2026, 11, 6, 0, 0, tzinfo=UTC)


def _normalize(
    account: GmailAccount,
    payload: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    normalized = normalize_snapshot(
        account, (), (payload,),
        ingested_at=_FIXED_TIME,
        dataset_version="recovery-parity-read-only",
        source_connector_version="recovery-parity-read-only",
    )
    return {name: normalized.rows(name) for name in CURRENT_PROJECTION_TABLES}


def _history_bucket(payload: dict[str, Any], target: str) -> str:
    value = payload.get("historyId")
    if not isinstance(value, str) or not value.isdecimal():
        return "unknown"
    return "gt_target" if int(value) > int(target) else "le_target"


def qualify(
    *,
    artifact_root: Path,
    account_id: str,
    email_address: str,
    baseline_run_id: UUID,
    delta_run_ids: tuple[UUID, ...],
    first_run_id: UUID,
    second_run_id: UUID,
    max_messages: int = 100,
) -> dict[str, object]:
    """Compare normalized rows in isolation from historical projection writes."""
    if not email_address or not account_id or first_run_id == second_run_id:
        raise ValueError("Distinct run IDs and account identity are required")
    ids, event_count, target = unresolved_ids(
        artifact_root, account_id, baseline_run_id, delta_run_ids
    )
    if max_messages < 1 or len(ids) > max_messages:
        raise ValueError("Offline comparison size exceeds safety cap")

    store = LocalArtifactStore(artifact_root)
    first, first_404, _ = _manifest(
        store, first_run_id, account_id=account_id,
        baseline_run_id=baseline_run_id, delta_run_ids=delta_run_ids,
        expected_ids=ids, target_history_id=target,
    )
    second, second_404, _ = _manifest(
        store, second_run_id, account_id=account_id,
        baseline_run_id=baseline_run_id, delta_run_ids=delta_run_ids,
        expected_ids=ids, target_history_id=target,
    )
    if first != second or first_404 != second_404:
        raise ValueError("Recovery source inventories differ")

    account = GmailAccount(
        account_id=account_id,
        email_address=email_address,
        messages_total=None,
        threads_total=None,
        history_id=target,
    )
    reader = RawMessageWriter(store, job_id=_JOB)
    raw_equal = raw_different = normalized_equal = normalized_different = 0
    normalized_different_groups: Counter[str] = Counter()
    raw_changed_buckets: Counter[str] = Counter()
    normalized_changed_buckets: Counter[str] = Counter()
    source_bucket_counts: Counter[str] = Counter()

    for message_id in sorted(first):
        raw_a = dict(reader.read(first_run_id, message_id))
        raw_b = dict(reader.read(second_run_id, message_id))
        bucket_a = _history_bucket(raw_a, target)
        bucket_b = _history_bucket(raw_b, target)
        if bucket_a != bucket_b:
            raise ValueError("Recovery capture crossed historical historyId boundary")
        source_bucket_counts[bucket_a] += 1
        if raw_a == raw_b:
            raw_equal += 1
            # Identical source payload deterministically yields identical normalized rows.
            normalized_equal += 1
            continue
        raw_different += 1
        raw_changed_buckets[bucket_a] += 1
        norm_a = _normalize(account, raw_a)
        norm_b = _normalize(account, raw_b)
        changed_tables = {
            table for table in CURRENT_PROJECTION_TABLES
            if logical_projection_digest(norm_a, table_names=(table,))
            != logical_projection_digest(norm_b, table_names=(table,))
        }
        if changed_tables:
            normalized_different += 1
            normalized_changed_buckets[bucket_a] += 1
            normalized_different_groups.update(changed_tables)
        else:
            normalized_equal += 1

    return {
        "status": "QUALIFIED_OFFLINE_COMPARISON",
        "historical_target_history_id": target,
        "historical_unresolved_add_events": event_count,
        "captured_messages_compared": len(first),
        "identical_full_raw": raw_equal,
        "different_full_raw": raw_different,
        "normalized_equivalent": normalized_equal,
        "normalized_different": normalized_different,
        "changed_normalized_table_counts": dict(sorted(normalized_different_groups.items())),
        "raw_changed_history_bucket_counts": dict(sorted(raw_changed_buckets.items())),
        "normalized_changed_history_bucket_counts": dict(sorted(normalized_changed_buckets.items())),
        "source_history_bucket_counts": dict(sorted(source_bucket_counts.items())),
        "still_unavailable_in_both": len(first_404),
        "historical_projection_qualified": False,
        "gmail_api_calls": 0,
        "persistent_writes": 0,
        "caveat": (
            "Normalization parity is only between two later captures. The evidence "
            "cannot reconstruct or certify exact historical content, labels or "
            "the three unavailable messages at the target frontier."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--email-address", required=True)
    parser.add_argument("--baseline-run-id", required=True, type=UUID)
    parser.add_argument("--delta-run-id", required=True, action="append", type=UUID)
    parser.add_argument("--first-recovery-run-id", required=True, type=UUID)
    parser.add_argument("--second-recovery-run-id", required=True, type=UUID)
    parser.add_argument("--max-messages", type=int, default=100)
    args = parser.parse_args()
    try:
        result = qualify(
            artifact_root=args.artifact_root, account_id=args.account_id,
            email_address=args.email_address,
            baseline_run_id=args.baseline_run_id,
            delta_run_ids=tuple(args.delta_run_id),
            first_run_id=args.first_recovery_run_id,
            second_run_id=args.second_recovery_run_id,
            max_messages=args.max_messages,
        )
    except (OSError, RuntimeError, ValueError, KeyError, TypeError) as exc:
        print(f"NORMALIZATION PARITY FAILED: exception_type={type(exc).__name__}")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
