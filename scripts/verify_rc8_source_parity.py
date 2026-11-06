#!/usr/bin/env python3
"""Read-only sample parity between RC8 candidate Parquet and live Gmail FULL.

No user message content is emitted, no source mutation, no Current promotion.
Sampling cannot qualify an entire mailbox. Every read is History-guarded.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pyarrow.parquet as pq
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion import __version__
from gmail_ingestion.api import GmailApiClient, GmailApiError, GmailHistoryExpiredError
from gmail_ingestion.auth import GmailAuthError, build_provider
from gmail_ingestion.config import default_credential_dir
from gmail_ingestion.normalization import normalize_snapshot
from gmail_ingestion.persistence.projection_dataset_store import NormalizedBaselineReader
from gmail_ingestion.projection.message_delta import (
    CURRENT_PROJECTION_TABLES, MESSAGE_PROJECTION_TABLES,
)
from gmail_ingestion.quality.rc8_source_parity import compare_message_bundle

from capture_rc8_current_source_evidence import _assert_stable_recovery_ids
from plan_rc8_current_candidate import _source


class RC8SourceSampleError(RuntimeError):
    """Missing provenance, corrupted Parquet, source drift or incorrect rows."""


def _hash_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def _load_rows(
    path: Path, selected: set[str], *, table: str,
) -> list[dict[str, Any]]:
    """Stream all row groups with a bounded in-memory selection."""
    if not path.is_file():
        raise RC8SourceSampleError("Missing candidate normalized Parquet table")
    rows: list[dict[str, Any]] = []
    for record_batch in pq.ParquetFile(path).iter_batches(batch_size=8192):
        for row in record_batch.to_pylist():
            if table == "threads" or row.get("message_id") in selected:
                rows.append(row)
    return rows


def selected_ids(source_ids: set[str], count: int) -> tuple[str, ...]:
    """A deterministic sample, independent from Parquet physical row order."""
    if not 1 <= count <= 500:
        raise ValueError("Sample size must be in the interval 1..500")
    return tuple(sorted(
        source_ids, key=lambda mid: hashlib.sha256(mid.encode()).digest()
    )[:count])


def validate_sample(
    *,
    account_id: str,
    artifact_root: Path,
    projection_root: Path,
    baseline_run_id: UUID,
    projection_id: str,
    scoped_inventory: Path,
    client: Any,
    sample_size: int = 32,
) -> dict[str, object]:
    """Compare all logical tables for sampled messages and close their drift window."""
    ids, source = _source(scoped_inventory)
    if source.get("account_id") != account_id:
        raise RC8SourceSampleError("Scoped source evidence account mismatch")
    chosen = selected_ids(ids, sample_size)
    target = source["history_end"]
    base = projection_root.resolve() / "accounts" / account_id / "projections"
    root = base / "candidate_data" / projection_id
    if not root.is_dir():
        raise RC8SourceSampleError("Candidate Current data directory is missing")
    provenance = json.loads((root / "rc8-provenance.json").read_text(encoding="utf-8"))
    dataset_manifest = json.loads((root / "projection-data.json").read_text(encoding="utf-8"))
    if (
        not isinstance(provenance, dict)
        or not isinstance(dataset_manifest, dict)
        or provenance.get("status") != "CANDIDATE_DATASET_NOT_SOURCE_QUALIFIED"
        or provenance.get("policy") != "RC8_CURRENT_OVERLAY_V1"
        or provenance.get("account_id") != account_id
        or provenance.get("baseline_run_id") != str(baseline_run_id)
        or provenance.get("projection_id") != projection_id
        or provenance.get("source_history_id") != target
        or provenance.get("source_id_set_sha256") != source["id_set_sha256"]
        or provenance.get("current_pointer_promoted") is not False
        or dataset_manifest.get("projection_id") != projection_id
        or dataset_manifest.get("logical_digest") != provenance.get("logical_digest")
    ):
        raise RC8SourceSampleError("Current candidate provenance differs from source")

    expected_hashes = provenance.get("parquet_sha256")
    if not isinstance(expected_hashes, dict) or set(expected_hashes) != set(CURRENT_PROJECTION_TABLES):
        raise RC8SourceSampleError("Physical Parquet checksums are incomplete")
    per_table: dict[str, list[dict[str, Any]]] = {}
    for table in CURRENT_PROJECTION_TABLES:
        path = root / "normalized" / f"{table}.parquet"
        if _hash_file(path) != expected_hashes[table]:
            raise RC8SourceSampleError("Current candidate Parquet checksum mismatch")
        per_table[table] = _load_rows(path, set(chosen), table=table)
    threads = {
        row["thread_id"] for row in per_table["threads"]
        if isinstance(row.get("thread_id"), str)
    }
    account = NormalizedBaselineReader(LocalArtifactStore(artifact_root)).read_account(
        baseline_run_id, account_id
    )
    for mid in chosen:
        response = client.messages.get_message(mid, format="full")
        value = response.get("historyId")
        if (
            response.get("id") != mid
            or not isinstance(value, str) or not value.isdecimal()
            or int(value) > int(target)
        ):
            raise RC8SourceSampleError("Gmail source message changed after inventory")
        source_norm = normalize_snapshot(
            account, (), (response,),
            ingested_at=datetime(1970, 1, 1, tzinfo=UTC),
            dataset_version="source-parity",
            source_connector_version=__version__,
        )
        compare_message_bundle(
            message_id=mid,
            source_rows={t: source_norm.rows(t) for t in MESSAGE_PROJECTION_TABLES},
            current_rows={
                t: [row for row in per_table[t] if row.get("message_id") == mid]
                for t in MESSAGE_PROJECTION_TABLES
            },
            thread_ids=threads,
        )
    checked_to = _assert_stable_recovery_ids(
        client, since=target, ids=set(chosen), max_pages=200, page_size=500
    )
    return {
        "status": "SAMPLE_PARITY_PASS_NOT_FULL_RELEASE_QUALIFICATION",
        "account_bound": True,
        "candidate_id": projection_id,
        "source_history_id": target,
        "history_checked_through": checked_to,
        "source_message_count": len(ids),
        "sample_requested": sample_size,
        "sample_verified": len(chosen),
        "table_count_verified": len(MESSAGE_PROJECTION_TABLES),
        "thread_references_verified": True,
        "mime_attachment_references_verified": True,
        "parquet_sha256_verified": True,
        "gmail_read_only": True,
        "full_mailbox_verified": False,
        "current_pointer_promoted": False,
        "stable_release_qualified": False,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--account-id", required=True)
    p.add_argument("--artifact-root", type=Path, required=True)
    p.add_argument("--projection-root", type=Path, required=True)
    p.add_argument("--baseline-run-id", type=UUID, required=True)
    p.add_argument("--projection-id", required=True)
    p.add_argument("--source-inventory", type=Path, required=True)
    p.add_argument("--sample-size", type=int, default=32)
    p.add_argument("--credential-dir", type=Path, default=default_credential_dir())
    args = p.parse_args()
    try:
        provider = build_provider(Path("credentials.json"), args.credential_dir)
        resolved, credentials = provider.load(args.account_id)
        if resolved != args.account_id:
            raise RC8SourceSampleError("Authenticated account differs from requested")
        evidence = validate_sample(
            account_id=args.account_id,
            artifact_root=args.artifact_root,
            projection_root=args.projection_root,
            baseline_run_id=args.baseline_run_id,
            projection_id=args.projection_id,
            scoped_inventory=args.source_inventory,
            client=GmailApiClient(credentials=credentials),
            sample_size=args.sample_size,
        )
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, GmailApiError, GmailAuthError) as exc:
        print("SOURCE SAMPLE PARITY FAILED: exception_type=" + type(exc).__name__)
        if isinstance(exc, GmailHistoryExpiredError):
            print("reason: Gmail History cursor expired; no snapshot attempted")
        return 2
    print(json.dumps(evidence, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
