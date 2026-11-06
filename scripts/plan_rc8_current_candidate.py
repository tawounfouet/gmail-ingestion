#!/usr/bin/env python3
"""RC8 isolated Current reconciliation plan and explicit missing-RAW ledger.

An offline, metadata-only candidate plan. Never rewrites historical RAW,
never builds a multi-GB projection, and cannot change the Current pointer.
The private ledger is create-once outside Git and must never be published.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections import Counter
from pathlib import Path
from typing import Any
from uuid import UUID

from compare_source_inventory_ids import read_baseline_ids
from observe_current_source_inventory import write_private_inventory
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.domain import GmailHistoryEventType
from gmail_ingestion.persistence import LocalProjectionBatchReader, RawMessageWriter
from gmail_ingestion.projection import validate_delta_chain


class RC8CandidateError(RuntimeError):
    """Fail-closed evidence/identity validation error."""


def _digest(ids: set[str]) -> str:
    return hashlib.sha256(("\n".join(sorted(ids)) + "\n").encode("utf-8")).hexdigest()


def _source(path: Path) -> tuple[set[str], dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RC8CandidateError("Expected source-scope evidence JSON object")
    ids = data.get("ids")
    if (
        data.get("schema_version") != 1
        or data.get("status") != "OBSERVED_NOT_RELEASE_QUALIFIED"
        or data.get("scope") != "include_spam_trash=false; unfiltered Gmail messages"
        or data.get("include_spam_trash") is not False
        or data.get("source_scope_evidence_version") != 1
        or data.get("history_catchup_complete") is not True
        or not isinstance(ids, list)
        or any(not isinstance(x, str) or not x for x in ids)
        or len(ids) != len(set(ids))
    ):
        raise RC8CandidateError("Incomplete or unsupported scoped source inventory")
    target = data.get("history_end")
    if not isinstance(target, str) or not target.isdecimal():
        raise RC8CandidateError("Scoped source frontier is invalid")
    full_set = set(ids)
    if data.get("message_count") != len(full_set) or data.get("id_set_sha256") != _digest(full_set):
        raise RC8CandidateError("Scoped source evidence count/digest mismatch")
    src_sha = data.get("source_all_inventory_sha256")
    if not isinstance(src_sha, str) or len(src_sha) != 64 or any(
        ch not in "0123456789abcdef" for ch in src_sha
    ):
        raise RC8CandidateError("Missing authenticated parent inventory digest")
    return full_set, data


def _baseline_manifest(
    store: LocalArtifactStore, account_id: str, baseline_run_id: UUID,
) -> None:
    path = store.run_root("gmail.messages", baseline_run_id) / "manifest.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RC8CandidateError("Snapshot manifest must be an object")
    filters = data.get("filters")
    if (
        data.get("run_id") != str(baseline_run_id)
        or data.get("account_id") != account_id
        or data.get("status") != "COMPLETED"
        or data.get("mode") != "full_snapshot"
        or data.get("include_spam_trash") is not False
        or data.get("message_limit") is not None
        or not isinstance(filters, dict)
        or filters.get("query") is not None
        or filters.get("label_ids") != []
        or any(filters.get(key) is not None for key in ("after", "before", "last_days"))
    ):
        raise RC8CandidateError("Historical baseline discovery scope does not match RC8")


def _classify_missing(
    events: list[Any], source_ids: set[str], raw_writer: RawMessageWriter,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    """Classify every missing event-local RAW, preserving source uncertainty."""
    missing: list[dict[str, str]] = []
    classes: Counter[str] = Counter()
    later_deleted: set[str] = set()
    ordered = sorted(events, key=lambda e: (int(e.history_id), e.event_ordinal, e.event_id))
    for event in reversed(ordered):
        if event.event_type is GmailHistoryEventType.MESSAGE_DELETED:
            later_deleted.add(event.message_id)
        elif event.event_type is GmailHistoryEventType.MESSAGE_ADDED:
            if raw_writer.exists(event.run_id, event.message_id):
                continue
            if event.message_id in later_deleted:
                kind = "SUPERSEDED_BY_COMMITTED_DELETE"
            elif event.message_id in source_ids:
                kind = "SOURCE_PRESENT_ORIGINAL_RAW_MISSING"
            else:
                kind = "ABSENT_FROM_SCOPED_SOURCE_UNPROVEN"
            classes[kind] += 1
            # Confidential: only in the private create-once local manifest.
            missing.append({
                "message_id": event.message_id,
                "event_id": event.event_id,
                "run_id": str(event.run_id),
                "history_id": event.history_id,
                "status": kind,
            })
    missing.sort(key=lambda e: (int(e["history_id"]), e["event_id"]))
    return missing, dict(sorted(classes.items()))


def _fold_id_membership(baseline_ids: set[str], events: list[Any]) -> set[str]:
    """Diagnostic-only fold: not a scoped projection (label moves are separate)."""
    ids = set(baseline_ids)
    for event in sorted(
        events, key=lambda e: (int(e.history_id), e.event_ordinal, e.event_id)
    ):
        if event.event_type is GmailHistoryEventType.MESSAGE_ADDED:
            ids.add(event.message_id)
        elif event.event_type is GmailHistoryEventType.MESSAGE_DELETED:
            ids.discard(event.message_id)
    return ids


def build_candidate_plan(
    *,
    artifact_root: Path,
    account_id: str,
    baseline_run_id: UUID,
    delta_run_ids: tuple[UUID, ...],
    source_inventory: Path,
    min_free_gib: int = 20,
) -> dict[str, Any]:
    """Return a deterministic, isolated, strictly unpromotable RC8 evidence plan."""
    if not delta_run_ids or len(set(delta_run_ids)) != len(delta_run_ids):
        raise RC8CandidateError("Ordered unique committed delta run IDs are required")
    if min_free_gib < 1:
        raise ValueError("min_free_gib must be positive")
    source_ids, source = _source(source_inventory)
    store = LocalArtifactStore(artifact_root)
    _baseline_manifest(store, account_id, baseline_run_id)
    baseline_ids, baseline_frontier, segments = read_baseline_ids(
        store, baseline_run_id, account_id
    )
    batches = LocalProjectionBatchReader(store).read_many(delta_run_ids)
    last_frontier = batches[-1].output_history_id
    source_frontier = source["history_end"]
    if int(last_frontier) < int(source_frontier):
        raise RC8CandidateError("Committed History chain does not cover source frontier")
    if int(source_frontier) < int(baseline_frontier):
        raise RC8CandidateError("Source frontier predates historical baseline")
    all_events = validate_delta_chain(
        account_id=account_id,
        start_history_id=baseline_frontier,
        target_history_id=last_frontier,
        batches=batches,
    )
    relevant = [e for e in all_events if int(e.history_id) <= int(source_frontier)]
    missing, classes = _classify_missing(
        relevant, source_ids, RawMessageWriter(store)
    )
    approximate_ids = _fold_id_membership(baseline_ids, relevant)
    source_only = source_ids - approximate_ids
    local_only = approximate_ids - source_ids
    label_moves = sum(
        e.event_type in {GmailHistoryEventType.LABEL_ADDED, GmailHistoryEventType.LABEL_REMOVED}
        and e.label_id in {"SPAM", "TRASH"} for e in relevant
    )

    ledger_canonical = json.dumps(missing, sort_keys=True, separators=(",", ":"))
    ledger_digest = hashlib.sha256(ledger_canonical.encode("utf-8")).hexdigest()
    identity = {
        "policy": "RC8_SOURCE_VALIDATED_CURRENT_V1",
        "account_id": account_id,
        "baseline_run_id": str(baseline_run_id),
        "delta_run_ids": [str(x) for x in delta_run_ids],
        "baseline_history_id": baseline_frontier,
        "source_history_id": source_frontier,
        "source_id_set_sha256": source["id_set_sha256"],
        "parent_inventory_sha256": source["source_all_inventory_sha256"],
        "exception_ledger_sha256": ledger_digest,
        "schema_version": 1,
    }
    canonical = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    candidate_id = "rc8_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]

    free_gib = round(shutil.disk_usage(artifact_root).free / 1024**3, 2)
    resource_gate = free_gib >= min_free_gib
    blocking = [
        "SOURCE_LABELS_THREADS_NOT_RECONCILED",
        "MESSAGE_MIME_REFERENCES_NOT_QUALIFIED",
        "DETERMINISTIC_FULL_REBUILD_NOT_QUALIFIED",
        "LIVE_HISTORY_CLOSURE_NOT_PROVEN",
    ]
    if any(e["status"] != "SUPERSEDED_BY_COMMITTED_DELETE" for e in missing):
        blocking.append("MISSING_ORIGINAL_RAW_REQUIRES_EXPLICIT_CURRENT_POLICY")
    if source_only or local_only:
        blocking.append("SCOPED_SOURCE_ID_PARITY_UNPROVEN")
    if not resource_gate:
        blocking.append("INSUFFICIENT_DISK_FOR_BULK_PROJECTION")

    return {
        "schema_version": 1,
        "candidate_mode": "CURRENT_RECONCILIATION",
        "candidate_id": candidate_id,
        "status": "BLOCKED_SOURCE_QUALIFICATION_REQUIRED",
        "source_history_id": source_frontier,
        "committed_history_id": last_frontier,
        "baseline_history_id": baseline_frontier,
        "baseline_run_id": str(baseline_run_id),
        "committed_delta_run_ids": [str(x) for x in delta_run_ids],
        "source_scope": source["scope"],
        "source_id_set_sha256": source["id_set_sha256"],
        "parent_inventory_sha256": source["source_all_inventory_sha256"],
        "exception_ledger_sha256": ledger_digest,
        "source_id_count": len(source_ids),
        "baseline_id_count": len(baseline_ids),
        "baseline_segments_verified": segments,
        "history_events_considered": len(relevant),
        "events_missing_original_raw": len(missing),
        "distinct_missing_message_ids": len({e["message_id"] for e in missing}),
        "missing_classifications": classes,
        "scope_label_change_events": label_moves,
        "id_fold_source_only_count": len(source_only),
        "id_fold_local_only_count": len(local_only),
        "id_fold_is_label_aware": False,
        "available_disk_gib": free_gib,
        "min_disk_gib": min_free_gib,
        "bulk_materialization_resource_gate": resource_gate,
        "blocking_gates": sorted(blocking),
        "rebuild_equal": False,
        "current_pointer_promoted": False,
        "stable_release_qualified": False,
        "gmail_api_calls": 0,
        "note": (
            "ID fold is a diagnostic upper-level approximation; SPAM/TRASH label "
            "moves and the missing original RAW require source-aware reconciliation. "
            "No message content, label or MIME correctness is claimed."
        ),
        "private_exception_ledger": missing,
        "private_recovery_required_ids": sorted(
            source_only | {
                item["message_id"] for item in missing
                if item["status"] == "SOURCE_PRESENT_ORIGINAL_RAW_MISSING"
            }
        ),
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--artifact-root", type=Path, required=True)
    p.add_argument("--account-id", required=True)
    p.add_argument("--baseline-run-id", required=True, type=UUID)
    p.add_argument("--delta-run-id", required=True, action="append", type=UUID)
    p.add_argument("--source-inventory", required=True, type=Path)
    p.add_argument("--candidate-evidence-out", required=True, type=Path)
    p.add_argument("--min-free-gib", type=int, default=20)
    a = p.parse_args()
    try:
        plan = build_candidate_plan(
            artifact_root=a.artifact_root, account_id=a.account_id,
            baseline_run_id=a.baseline_run_id,
            delta_run_ids=tuple(a.delta_run_id),
            source_inventory=a.source_inventory,
            min_free_gib=a.min_free_gib,
        )
        write_private_inventory(a.candidate_evidence_out, plan)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        print("RC8 CANDIDATE FAILED: exception_type=" + type(exc).__name__)
        return 2
    public = {k: v for k, v in plan.items() if not k.startswith("private_")}
    print(json.dumps(public, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
