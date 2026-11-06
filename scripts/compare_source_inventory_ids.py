#!/usr/bin/env python3
"""Read-only Gmail message-ID parity audit at one observed History frontier.

This does not project message bodies, MIME, labels or threads, and never
advances a checkpoint or promotes Current. Fail closed if source and baseline
snapshot have incompatible discovery scopes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from uuid import UUID

import pyarrow.parquet as pq
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.domain import GmailHistoryEvent, GmailHistoryEventType
from gmail_ingestion.persistence import LocalProjectionBatchReader
from gmail_ingestion.projection import validate_delta_chain


class InventoryParityError(RuntimeError):
    """Invalid evidence, incompatible scope or incomplete source coverage."""


class InventoryScopeMismatch(InventoryParityError):
    """A Gmail inventory cannot be compared to a differently scoped baseline."""


class HistoryCoverageMissing(InventoryParityError):
    """Committed delta coverage has not reached the source observation."""


def _json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise InventoryParityError("Evidence file must be a JSON object")
    return data


def _digest_ids(ids: set[str]) -> str:
    return hashlib.sha256(("\n".join(sorted(ids)) + "\n").encode("utf-8")).hexdigest()


def read_source_ids(path: Path) -> tuple[set[str], dict[str, Any]]:
    """Validate the complete local inventory before trusting its counts or digest."""
    doc = _json(path)
    if (
        doc.get("schema_version") != 1
        or doc.get("status") != "OBSERVED_NOT_RELEASE_QUALIFIED"
        or doc.get("history_catchup_complete") is not True
        or doc.get("scope")
        != "include_spam_trash=true; unfiltered Gmail messages"
        or doc.get("current_projection_compared") is not False
    ):
        raise InventoryParityError("Unrecognized or incomplete Gmail source observation")
    start, end = doc.get("history_start"), doc.get("history_end")
    if (
        not isinstance(start, str)
        or not start.isdecimal()
        or not isinstance(end, str)
        or not end.isdecimal()
        or int(end) < int(start)
    ):
        raise InventoryParityError("Invalid source History observation frontiers")
    values = doc.get("ids")
    if not isinstance(values, list) or not all(isinstance(x, str) and x for x in values):
        raise InventoryParityError("Invalid source identifier inventory")
    if len(values) != len(set(values)):
        raise InventoryParityError("Source inventory contains duplicate IDs")
    ids = set(values)
    if (
        doc.get("message_count") != len(ids)
        or doc.get("id_set_sha256") != _digest_ids(ids)
    ):
        raise InventoryParityError("Source inventory count or digest mismatch")
    return ids, doc


def verify_baseline_scope(
    store: LocalArtifactStore, baseline_run_id: UUID, account_id: str
) -> dict[str, Any]:
    """Require the original snapshot itself to be an unfiltered all-mail run."""
    source = _json(store.run_root("gmail.messages", baseline_run_id) / "manifest.json")
    if (
        source.get("run_id") != str(baseline_run_id)
        or source.get("account_id") != account_id
        or source.get("status") != "COMPLETED"
        or source.get("mode") != "full_snapshot"
        or source.get("message_limit") is not None
        or source.get("filters", {}).get("query") is not None
        or source.get("filters", {}).get("label_ids") != []
    ):
        raise InventoryScopeMismatch("Baseline is not an unfiltered completed full snapshot")
    if source.get("include_spam_trash") is not True:
        raise InventoryScopeMismatch(
            "Original baseline excluded Spam/Trash but source inventory includes them"
        )
    return source


def read_baseline_ids(
    store: LocalArtifactStore, baseline_run_id: UUID, account_id: str
) -> tuple[set[str], str, int]:
    """Stream only the ID column of each committed, hash-verified Parquet segment."""
    root = store.run_root("gmail.projection_baseline", baseline_run_id)
    manifest = _json(root / "baseline_manifest.json")
    if (
        manifest.get("layout") != "segmented-parquet-v1"
        or manifest.get("status") != "COMPLETED"
        or manifest.get("account_id") != account_id
        or manifest.get("source_snapshot_run_id") != str(baseline_run_id)
    ):
        raise InventoryParityError("Invalid completed segmented baseline provenance")
    segments, total, batch_size = (
        manifest.get("segment_count"),
        manifest.get("total_messages"),
        manifest.get("batch_size"),
    )
    if (
        not isinstance(segments, int) or isinstance(segments, bool)
        or not isinstance(total, int) or isinstance(total, bool)
        or not isinstance(batch_size, int) or isinstance(batch_size, bool)
        or segments < 1 or total < 1 or batch_size < 1
        or segments != (total + batch_size - 1) // batch_size
    ):
        raise InventoryParityError("Invalid baseline segment frontier metadata")
    checkpoint = manifest.get("history_id")
    if not isinstance(checkpoint, str) or not checkpoint.isdecimal():
        raise InventoryParityError("Missing baseline History frontier")

    ids: set[str] = set()
    rows_seen = 0
    for index in range(segments):
        segment = root / "segments" / f"{index:08d}"
        evidence = _json(segment / "segment.json")
        file = segment / "messages.parquet"
        if (
            evidence.get("source_snapshot_run_id") != str(baseline_run_id)
            or evidence.get("layout") != "segmented-parquet-v1"
            or evidence.get("segment_index") != index
            or evidence.get("start") != index * batch_size
            or evidence.get("stop") != min((index + 1) * batch_size, total)
        ):
            raise InventoryParityError("Baseline segment identity or frontier mismatch")
        hash_state = hashlib.sha256()
        with file.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hash_state.update(chunk)
        if evidence.get("sha256", {}).get("messages") != hash_state.hexdigest():
            raise InventoryParityError("Baseline messages segment digest mismatch")
        parquet = pq.ParquetFile(file)
        if parquet.metadata.num_rows != evidence.get("row_counts", {}).get("messages"):
            raise InventoryParityError("Baseline messages segment row count mismatch")
        for record_batch in parquet.iter_batches(columns=["message_id"], batch_size=8192):
            for message_id in record_batch.column(0).to_pylist():
                if not isinstance(message_id, str) or not message_id:
                    raise InventoryParityError("Invalid baseline message identity")
                if message_id in ids:
                    raise InventoryParityError("Duplicate baseline message identity")
                ids.add(message_id)
                rows_seen += 1
    if rows_seen != total or manifest.get("row_counts", {}).get("messages") != rows_seen:
        raise InventoryParityError("Incomplete baseline message ID inventory")
    return ids, checkpoint, segments


def reduce_ids(
    baseline_ids: set[str],
    events: Sequence[GmailHistoryEvent],
    target_history_id: str,
) -> tuple[set[str], dict[str, int]]:
    """Apply committed History message events to source frontier (ID-level only)."""
    target = int(target_history_id)
    ids = set(baseline_ids)
    adds = deletes = 0
    for event in sorted(
        events, key=lambda e: (int(e.history_id), e.event_ordinal, e.event_id)
    ):
        if int(event.history_id) > target:
            continue
        if event.event_type is GmailHistoryEventType.MESSAGE_ADDED:
            ids.add(event.message_id)
            adds += 1
        elif event.event_type is GmailHistoryEventType.MESSAGE_DELETED:
            ids.discard(event.message_id)
            deletes += 1
    return ids, {"message_add_events_applied": adds, "message_delete_events_applied": deletes}


def audit(
    *,
    artifact_root: Path,
    account_id: str,
    baseline_run_id: UUID,
    delta_run_ids: tuple[UUID, ...],
    source_inventory: Path,
) -> dict[str, Any]:
    if not delta_run_ids or len(set(delta_run_ids)) != len(delta_run_ids):
        raise InventoryParityError("Distinct committed delta runs are required")
    source_ids, source = read_source_ids(source_inventory)
    store = LocalArtifactStore(artifact_root)
    verify_baseline_scope(store, baseline_run_id, account_id)
    batches = LocalProjectionBatchReader(store).read_many(delta_run_ids)
    target = batches[-1].output_history_id
    source_target = source["history_end"]
    if int(target) < int(source_target):
        raise HistoryCoverageMissing(
            f"Committed History frontier {target} is before observed Gmail {source_target}"
        )

    baseline_ids, start, segments = read_baseline_ids(store, baseline_run_id, account_id)
    if int(source_target) < int(start):
        raise InventoryParityError("Observed Gmail frontier precedes baseline")
    events = validate_delta_chain(
        account_id=account_id,
        start_history_id=start,
        target_history_id=target,
        batches=batches,
    )
    local_ids, counters = reduce_ids(baseline_ids, events, source_target)
    source_only = source_ids - local_ids
    local_only = local_ids - source_ids
    same = not source_only and not local_only
    return {
        "status": "PASS_ID_MEMBERSHIP_ONLY" if same else "FAIL_ID_MEMBERSHIP_MISMATCH",
        "scope_compatible": True,
        "source_history_id": source_target,
        "committed_history_id": target,
        "partial_last_run_replayed_to_source_frontier": target != source_target,
        "baseline_history_id": start,
        "baseline_segments_read": segments,
        "baseline_message_ids": len(baseline_ids),
        "committed_delta_runs": len(delta_run_ids),
        "events_checked": len(events),
        "source_message_ids": len(source_ids),
        "local_message_ids": len(local_ids),
        "source_only_id_count": len(source_only),
        "local_only_id_count": len(local_only),
        "source_id_set_sha256": source["id_set_sha256"],
        "local_id_set_sha256": _digest_ids(local_ids),
        "gmail_api_calls": 0,
        "persistent_writes": 0,
        "private_message_ids_in_stdout": False,
        "message_content_qualified": False,
        "labels_threads_qualified": False,
        "current_projection_qualified": False,
        "stable_release_qualified": False,
    } | counters


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--baseline-run-id", type=UUID, required=True)
    parser.add_argument("--delta-run-id", action="append", type=UUID, required=True)
    parser.add_argument("--source-inventory", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = audit(
            artifact_root=args.artifact_root,
            account_id=args.account_id,
            baseline_run_id=args.baseline_run_id,
            delta_run_ids=tuple(args.delta_run_id),
            source_inventory=args.source_inventory,
        )
    except InventoryScopeMismatch as exc:
        print(f"NO_GO_SCOPE_MISMATCH: {exc}")
        return 3
    except HistoryCoverageMissing as exc:
        print(f"PENDING_HISTORY_CATCHUP: {exc}")
        return 3
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(f"ID PARITY FAILED: exception_type={type(exc).__name__}")
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["status"] == "PASS_ID_MEMBERSHIP_ONLY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
