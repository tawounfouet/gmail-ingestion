#!/usr/bin/env python3
"""Exhaustive local RC8 candidate audit, read-only and Gmail API free.

Verifies every normalized Parquet row and the frozen source ID set.
Independent live Gmail label/thread/MIME parity remains a separate gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from plan_rc8_current_candidate import _source

from gmail_ingestion.persistence import LocalProjectionStore
from gmail_ingestion.projection.message_delta import CURRENT_PROJECTION_TABLES
from gmail_ingestion.schemas import TABLE_SORT_KEYS


class RC8CandidateAuditError(RuntimeError):
    """A source, digest, row-count or relation invariant failed."""


_NON_LOGICAL_FIELDS = frozenset({
    "dataset_version", "source_connector_version", "ingested_at",
    "updated_at", "profile_fetched_at", "downloaded_at",
})


def _key(row: dict[str, Any], name: str) -> tuple[str, ...]:
    result: list[str] = []
    for field in TABLE_SORT_KEYS[name]:
        value = row.get(field)
        result.append(
            value.isoformat() if isinstance(value, datetime)
            else "" if value is None else str(value)
        )
    return tuple(result)


def _physical(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def audit_candidate(
    *, projection_root: Path, account_id: str, projection_id: str,
    source_inventory: Path, batch_size: int = 2048,
) -> dict[str, Any]:
    if not 1 <= batch_size <= 16384:
        raise RC8CandidateAuditError("Invalid audit batch size")
    source_ids, source = _source(source_inventory)
    if source.get("account_id") != account_id:
        raise RC8CandidateAuditError("Source account identity mismatch")
    store = LocalProjectionStore(projection_root)
    if store.load_current(account_id) is not None:
        raise RC8CandidateAuditError("Isolated root has Current pointer")
    root = store.candidate_data_dir(account_id, projection_id)
    provenance = json.loads((root / "rc8-provenance.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "projection-data.json").read_text(encoding="utf-8"))
    if (
        provenance.get("status") != "CANDIDATE_DATASET_NOT_SOURCE_QUALIFIED"
        or provenance.get("policy") != "RC8_CURRENT_OVERLAY_V1"
        or provenance.get("projection_id") != projection_id
        or provenance.get("source_history_id") != source["history_end"]
        or provenance.get("source_id_set_sha256") != source["id_set_sha256"]
        or provenance.get("account_id") != account_id
        or provenance.get("current_pointer_promoted") is not False
        or provenance.get("stable_release_qualified") is not False
        or manifest.get("projection_id") != projection_id
        or manifest.get("logical_digest") != provenance.get("logical_digest")
        or manifest.get("status") != "CANDIDATE"
        or set(manifest.get("tables", [])) != set(CURRENT_PROJECTION_TABLES)
    ):
        raise RC8CandidateAuditError("Candidate provenance and scope mismatch")
    if (
        not isinstance(provenance.get("parquet_sha256"), dict)
        or set(provenance["parquet_sha256"]) != set(CURRENT_PROJECTION_TABLES)
    ):
        raise RC8CandidateAuditError("Missing candidate table digests")

    digest = hashlib.sha256()
    digest.update(b"{")
    count: dict[str, int] = {}
    candidate_ids: set[str] = set()
    thread_counts: Counter[str] = Counter()
    declared_threads: dict[str, int] = {}
    message_thread_ids: set[str] = set()
    mime_keys: set[tuple[str, str]] = set()
    mime_parents: set[tuple[str, str]] = set()
    attachment_parts: set[tuple[str, str]] = set()
    all_names = sorted(CURRENT_PROJECTION_TABLES)
    for index, name in enumerate(all_names):
        path = root / "normalized" / f"{name}.parquet"
        if not path.is_file() or _physical(path) != provenance["parquet_sha256"][name]:
            raise RC8CandidateAuditError("Candidate Parquet integrity mismatch")
        if index:
            digest.update(b",")
        digest.update(json.dumps(name, ensure_ascii=False).encode())
        digest.update(b":[")
        previous: tuple[str, ...] | None = None
        n = 0
        for batch in pq.ParquetFile(path).iter_batches(batch_size=batch_size):
            for row in batch.to_pylist():
                if row.get("account_id") != account_id:
                    raise RC8CandidateAuditError("Candidate row belongs to another account")
                order = _key(row, name)
                if previous is not None and order < previous:
                    raise RC8CandidateAuditError("Candidate Parquet is not logically sorted")
                previous = order
                if n:
                    digest.update(b",")
                canonical = {
                    k: v.isoformat() if isinstance(v, datetime) else v
                    for k, v in row.items()
                    if k not in _NON_LOGICAL_FIELDS
                }
                digest.update(json.dumps(
                    canonical, sort_keys=True, separators=(",", ":"),
                    ensure_ascii=False,
                ).encode())
                n += 1
                mid = row.get("message_id")
                if name == "messages":
                    if not isinstance(mid, str) or mid in candidate_ids:
                        raise RC8CandidateAuditError("Duplicate/invalid candidate message")
                    candidate_ids.add(mid)
                    tid = row.get("thread_id")
                    if not isinstance(tid, str) or not tid:
                        raise RC8CandidateAuditError("Candidate message lacks thread")
                    thread_counts[tid] += 1
                    message_thread_ids.add(tid)
                elif name == "threads":
                    tid = row.get("thread_id")
                    if not isinstance(tid, str) or tid in declared_threads:
                        raise RC8CandidateAuditError("Duplicate candidate thread")
                    declared_threads[tid] = int(row["message_count"])
                else:
                    if not isinstance(mid, str) or mid not in source_ids:
                        raise RC8CandidateAuditError("Current child outside scoped source")
                    if name == "message_labels" and (
                        row.get("is_active") is True
                        and row.get("label_id") in {"SPAM", "TRASH"}
                    ):
                        raise RC8CandidateAuditError("Excluded active label in Current")
                    if name == "mime_parts":
                        key = (mid, str(row.get("part_path")))
                        if key in mime_keys:
                            raise RC8CandidateAuditError("Duplicate MIME part path")
                        mime_keys.add(key)
                        if row.get("parent_part_path") is not None:
                            mime_parents.add((mid, str(row["parent_part_path"])))
                    if name == "attachments":
                        attachment_parts.add((mid, str(row["part_path"])))
        digest.update(b"]")
        count[name] = n
        if manifest["row_counts"].get(name) != n:
            raise RC8CandidateAuditError("Parquet row count mismatch")
    digest.update(b"}")
    result_digest = digest.hexdigest()
    if (
        result_digest != provenance["logical_digest"]
        or result_digest != manifest["logical_digest"]
    ):
        raise RC8CandidateAuditError("Full logical digest mismatch")
    if candidate_ids != source_ids or count["messages"] != len(source_ids):
        raise RC8CandidateAuditError("Current source ID set differs")
    if thread_counts != declared_threads or message_thread_ids != set(declared_threads):
        raise RC8CandidateAuditError("Thread membership/count mismatch")
    if not mime_parents.issubset(mime_keys) or not attachment_parts.issubset(mime_keys):
        raise RC8CandidateAuditError("MIME graph/attachment references mismatch")
    if provenance.get("replay_digest") != result_digest:
        raise RC8CandidateAuditError("Independent replay digest mismatch")
    return {
        "status": "OFFLINE_CURRENT_PARITY_PASS_LIVE_SOURCE_NOT_QUALIFIED",
        "candidate_id": projection_id,
        "source_history_id": source["history_end"],
        "source_message_count": len(source_ids),
        "source_id_set_equal": True,
        "parquet_sha256_verified": True,
        "logical_digest_verified": True,
        "independent_replay_digest_verified": True,
        "threads_membership_verified": True,
        "mime_attachment_refs_verified": True,
        "row_counts": count,
        "gmail_api_calls": 0,
        "independent_gmail_labels_threads_mime_verified": False,
        "current_pointer_promoted": False,
        "stable_release_qualified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projection-root", type=Path, required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--projection-id", required=True)
    parser.add_argument("--source-inventory", type=Path, required=True)
    parser.add_argument("--batch-size", type=int, default=2048)
    args = parser.parse_args()
    try:
        report = audit_candidate(
            projection_root=args.projection_root, account_id=args.account_id,
            projection_id=args.projection_id, source_inventory=args.source_inventory,
            batch_size=args.batch_size,
        )
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        print("RC8 CURRENT AUDIT FAILED: exception_type=" + type(exc).__name__)
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
