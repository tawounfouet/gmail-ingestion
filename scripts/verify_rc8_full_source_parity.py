#!/usr/bin/env python3
"""Exhaustive read-only Gmail FULL parity over a frozen RC8 candidate.

Explicitly opt-in because this may require over 180,000 Gmail FULL GET calls.
No user content or Gmail message IDs are printed or persisted. This is a
source-consistency gate, not permission to promote an authoritative Current.
"""

from __future__ import annotations

import argparse
import itertools
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq
from capture_rc8_current_source_evidence import _assert_stable_recovery_ids
from plan_rc8_current_candidate import _source
from pyingestkit.artifacts.filesystem import LocalArtifactStore
from verify_rc8_source_parity import _hash_file

from gmail_ingestion import __version__
from gmail_ingestion.api import GmailApiClient, GmailApiError, GmailHistoryExpiredError
from gmail_ingestion.auth import GmailAuthError, build_provider
from gmail_ingestion.config import default_credential_dir
from gmail_ingestion.normalization import normalize_snapshot
from gmail_ingestion.persistence import LocalProjectionStore
from gmail_ingestion.persistence.projection_dataset_store import NormalizedBaselineReader
from gmail_ingestion.projection.message_delta import (
    CURRENT_PROJECTION_TABLES,
    MESSAGE_PROJECTION_TABLES,
)
from gmail_ingestion.quality.rc8_source_parity import compare_message_bundle


class RC8FullSourceParityError(RuntimeError):
    """Source drift, incomplete candidate, or full source parity violation."""


def _grouped(path: Path, *, batch_size: int) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    """Merge-joinable stream of all Parquet rows by message ID in bounded batches."""
    if not path.is_file():
        raise RC8FullSourceParityError("Missing candidate table")
    row_stream = (
        row
        for batch in pq.ParquetFile(path).iter_batches(batch_size=batch_size)
        for row in batch.to_pylist()
    )
    previous: str | None = None
    for mid, group in itertools.groupby(row_stream, key=lambda r: r.get("message_id")):
        if not isinstance(mid, str) or not mid:
            raise RC8FullSourceParityError("Malformed candidate message key")
        if previous is not None and mid <= previous:
            raise RC8FullSourceParityError("Candidate table is not sorted by message ID")
        previous = mid
        yield mid, list(group)


def _all_threads(path: Path, *, batch_size: int) -> set[str]:
    ids: set[str] = set()
    for batch in pq.ParquetFile(path).iter_batches(batch_size=batch_size):
        for row in batch.to_pylist():
            tid = row.get("thread_id")
            if not isinstance(tid, str) or not tid or tid in ids:
                raise RC8FullSourceParityError("Invalid or duplicate candidate thread")
            ids.add(tid)
    return ids


def validate_full_source(
    *, client: Any, account_id: str, artifact_root: Path, projection_root: Path,
    baseline_run_id: Any, projection_id: str, scoped_inventory: Path,
    max_messages: int = 1000, batch_size: int = 2048,
    max_history_pages: int = 1000,
) -> dict[str, Any]:
    """Stream local candidate rows once and compare every frozen Gmail ID."""
    if max_messages < 1 or not 1 <= batch_size <= 16384 or max_history_pages < 1:
        raise RC8FullSourceParityError("Invalid explicit exhaustive scan bounds")
    ids, source = _source(scoped_inventory)
    if source.get("account_id") != account_id or len(ids) > max_messages:
        raise RC8FullSourceParityError("Source account or explicit GET cap mismatch")
    root = (
        projection_root.resolve() / "accounts" / account_id
        / "projections" / "candidate_data" / projection_id
    )
    provenance = json.loads((root / "rc8-provenance.json").read_text(encoding="utf-8"))
    manifest = json.loads((root / "projection-data.json").read_text(encoding="utf-8"))
    if (
        provenance.get("policy") != "RC8_CURRENT_OVERLAY_V1"
        or provenance.get("status") != "CANDIDATE_DATASET_NOT_SOURCE_QUALIFIED"
        or provenance.get("projection_id") != projection_id
        or provenance.get("account_id") != account_id
        or provenance.get("source_history_id") != source["history_end"]
        or provenance.get("source_id_set_sha256") != source["id_set_sha256"]
        or provenance.get("baseline_run_id") != str(baseline_run_id)
        or provenance.get("current_pointer_promoted") is not False
        or manifest.get("logical_digest") != provenance.get("logical_digest")
        or manifest.get("projection_id") != projection_id
    ):
        raise RC8FullSourceParityError("Candidate/source provenance mismatch")
    control = LocalProjectionStore(projection_root)
    if control.load_current(account_id) is not None:
        raise RC8FullSourceParityError("Current pointer present in isolated root")
    hashes = provenance.get("parquet_sha256")
    if not isinstance(hashes, dict) or set(hashes) != set(CURRENT_PROJECTION_TABLES):
        raise RC8FullSourceParityError("Current candidate hashes missing")
    for name in CURRENT_PROJECTION_TABLES:
        path = root / "normalized" / f"{name}.parquet"
        if not path.is_file() or _hash_file(path) != hashes[name]:
            raise RC8FullSourceParityError("Candidate physical Parquet digest mismatch")

    thread_ids = _all_threads(root / "normalized" / "threads.parquet", batch_size=batch_size)
    readers = {
        name: iter(_grouped(
            root / "normalized" / f"{name}.parquet", batch_size=batch_size
        ))
        for name in MESSAGE_PROJECTION_TABLES
    }
    positions: dict[str, tuple[str, list[dict[str, Any]]] | None] = {
        name: next(reader, None) for name, reader in readers.items()
    }
    account = NormalizedBaselineReader(
        LocalArtifactStore(artifact_root)
    ).read_account(baseline_run_id, account_id)
    target = source["history_end"]
    if not isinstance(target, str) or not target.isdecimal():
        raise RC8FullSourceParityError("Invalid source History frontier")
    scanned = 0
    for mid in sorted(ids):
        current: dict[str, list[dict[str, Any]]] = {}
        for name in MESSAGE_PROJECTION_TABLES:
            position = positions[name]
            if position is not None and position[0] < mid:
                raise RC8FullSourceParityError("Candidate has foreign message ID")
            if position is not None and position[0] == mid:
                current[name] = position[1]
                positions[name] = next(readers[name], None)
            else:
                current[name] = []
        if len(current["messages"]) != 1:
            raise RC8FullSourceParityError("Candidate message row missing/duplicate")
        response = client.messages.get_message(mid, format="full")
        history_id = response.get("historyId")
        labels = response.get("labelIds")
        if (
            response.get("id") != mid
            or not isinstance(history_id, str) or not history_id.isdecimal()
            or int(history_id) > int(target)
            or not isinstance(labels, list)
            or "SPAM" in labels or "TRASH" in labels
        ):
            raise RC8FullSourceParityError("Gmail message drifted since frozen frontier")
        normalized = normalize_snapshot(
            account, (), (response,),
            ingested_at=datetime(1970, 1, 1, tzinfo=UTC),
            dataset_version="full-source-parity",
            source_connector_version=__version__,
        )
        compare_message_bundle(
            message_id=mid,
            source_rows={name: normalized.rows(name) for name in MESSAGE_PROJECTION_TABLES},
            current_rows=current,
            thread_ids=thread_ids,
        )
        scanned += 1
    if any(value is not None for value in positions.values()):
        raise RC8FullSourceParityError("Current candidate contains extra message rows")

    # This History check covers *all* source IDs, not only the sampled cohort.
    # It must fail closed on pagination expiration, concurrent mutations, or
    # an incomplete history window.
    closed_at = _assert_stable_recovery_ids(
        client, since=target, ids=ids, max_pages=max_history_pages,
        page_size=500,
    )
    return {
        "status": "EXHAUSTIVE_SOURCE_PARITY_PASS_RELEASE_REVIEW_REQUIRED",
        "candidate_id": projection_id,
        "source_history_id": target,
        "history_checked_through": closed_at,
        "source_message_count": len(ids),
        "full_messages_verified": scanned,
        "candidate_table_count": len(CURRENT_PROJECTION_TABLES),
        "parquet_sha256_verified": True,
        "source_ids_labels_threads_mime_verified": True,
        "gmail_api_readonly": True,
        "full_mailbox_verified": True,
        "current_pointer_promoted": False,
        "stable_release_qualified": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--projection-root", type=Path, required=True)
    parser.add_argument("--baseline-run-id", required=True)
    parser.add_argument("--projection-id", required=True)
    parser.add_argument("--source-inventory", type=Path, required=True)
    parser.add_argument("--credential-dir", type=Path, default=default_credential_dir())
    parser.add_argument("--max-messages", type=int, default=1000)
    parser.add_argument("--batch-size", type=int, default=2048)
    parser.add_argument("--max-history-pages", type=int, default=1000)
    parser.add_argument("--allow-exhaustive-full-reads", action="store_true")
    args = parser.parse_args()
    if not args.allow_exhaustive_full_reads:
        print("RC8 FULL SOURCE SCAN REFUSED: explicit opt-in required")
        return 2
    try:
        from uuid import UUID

        baseline = UUID(args.baseline_run_id)
        provider = build_provider(Path("credentials.json"), args.credential_dir)
        resolved, credentials = provider.load(args.account_id)
        if resolved != args.account_id:
            raise RC8FullSourceParityError("Authenticated account mismatch")
        result = validate_full_source(
            client=GmailApiClient(credentials=credentials),
            account_id=args.account_id, artifact_root=args.artifact_root,
            projection_root=args.projection_root, baseline_run_id=baseline,
            projection_id=args.projection_id, scoped_inventory=args.source_inventory,
            max_messages=args.max_messages, batch_size=args.batch_size,
            max_history_pages=args.max_history_pages,
        )
    except Exception as exc:  # noqa: BLE001 - never emit private Gmail context
        print("RC8 FULL SOURCE PARITY FAILED: exception_type=" + type(exc).__name__)
        if isinstance(exc, GmailHistoryExpiredError):
            print("reason=SOURCE_HISTORY_EXPIRED")
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
