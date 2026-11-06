#!/usr/bin/env python
"""Diagnose a bounded legacy RAW snapshot slice without writing artifacts or calling Gmail.

Only structural status and exception classes are printed. No message bodies, subjects,
addresses, OAuth credentials, or Gmail message IDs are included in the report.
"""

from __future__ import annotations

import argparse
import importlib
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion._version import __version__
from gmail_ingestion.control import MessageDiscoveryQueue
from gmail_ingestion.domain import GmailAccount, MessageFetchStatus
from gmail_ingestion.normalization import normalize_snapshot
from gmail_ingestion.persistence.raw_message_writer import RawMessageWriter
from gmail_ingestion.projection.message_delta import MESSAGE_PROJECTION_TABLES
from gmail_ingestion.schemas import TABLE_CONTRACTS


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Diagnose selected 0.4 snapshot messages locally; never writes artifacts."
    )
    parser.add_argument("--artifact-root", required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--snapshot-run-id", type=UUID, required=True)
    parser.add_argument("--email-address", required=True)
    parser.add_argument("--start-index", type=int, default=89500)
    parser.add_argument("--stop-index", type=int, default=95000)
    parser.add_argument("--batch-size", type=int, default=50)
    return parser.parse_args()


def _arrow_type(pa: Any, field_type: str) -> Any:
    types = {
        "string": pa.string,
        "int32": pa.int32,
        "int64": pa.int64,
        "boolean": pa.bool_,
    }
    if field_type == "timestamp[us, UTC]":
        return pa.timestamp("us", tz="UTC")
    if field_type not in types:
        raise ValueError("Unsupported normalized field type")
    return types[field_type]()


def _validate_arrow(
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    pa = importlib.import_module("pyarrow")
    pq = importlib.import_module("pyarrow.parquet")
    for name in MESSAGE_PROJECTION_TABLES:
        schema = pa.schema(
            [
                pa.field(field.name, _arrow_type(pa, field.type), nullable=field.nullable)
                for field in TABLE_CONTRACTS[name].fields
            ]
        )
        data = [dict(row) for row in rows.get(name, ())]
        if not data:
            continue
        table = pa.Table.from_pylist(data, schema=schema)
        sink = pa.BufferOutputStream()
        pq.write_table(table, sink)
        sink.close()


def diagnose(
    *,
    artifact_root: str,
    account_id: str,
    snapshot_run_id: UUID,
    email_address: str,
    start_index: int,
    stop_index: int,
    batch_size: int,
) -> int:
    """Return 0 on a clean slice and 1 on first failure, with no persistent writes."""
    if batch_size < 1 or start_index < 0 or stop_index <= start_index:
        raise ValueError("Require batch_size >= 1 and 0 <= start_index < stop_index")
    store = LocalArtifactStore(artifact_root)
    queue = MessageDiscoveryQueue.load_or_create(
        store, snapshot_run_id, account_id, None
    )
    if not queue.loaded_existing or not queue.discovery_complete or queue.history_start is None:
        raise ValueError("Full snapshot discovery state is missing or incomplete")
    records = sorted(queue.records.values(), key=lambda record: record.message_id)
    total = len(records)
    if start_index >= total:
        raise ValueError("start_index is beyond snapshot size")
    end_index = min(stop_index, total)
    account = GmailAccount(
        account_id=account_id,
        email_address=email_address,
        messages_total=total,
        threads_total=None,
        history_id=queue.history_start,
    )
    ingested_at: datetime = min(record.discovered_at for record in records)
    raw = RawMessageWriter(store)
    print(f"snapshot_records: {total}")
    print(f"diagnostic_range: [{start_index}, {end_index})")
    print("api_calls: 0")
    print("persistent_writes: 0")

    for start in range(start_index, end_index, batch_size):
        stop = min(start + batch_size, end_index)
        stage = "read-raw"
        try:
            payloads: list[Mapping[str, Any]] = []
            for record in records[start:stop]:
                if record.fetch_status is not MessageFetchStatus.FETCHED:
                    raise ValueError("A discovery record is not marked FETCHED")
                payload = raw.read(snapshot_run_id, record.message_id)
                if payload.get("threadId") != record.thread_id:
                    raise ValueError("RAW thread ID does not match discovery evidence")
                payloads.append(payload)

            stage = "normalize"
            normalized = normalize_snapshot(
                account,
                (),
                payloads,
                ingested_at=ingested_at,
                dataset_version=str(snapshot_run_id),
                source_connector_version=__version__,
            )
            stage = "arrow-parquet-memory"
            _validate_arrow(
                {name: normalized.rows(name) for name in MESSAGE_PROJECTION_TABLES}
            )
        except Exception as exc:  # noqa: BLE001 - bounded diagnostic boundary
            print("result: FAIL")
            print(f"failing_range: [{start}, {stop})")
            print(f"stage: {stage}")
            print(f"exception_type: {type(exc).__name__}")
            errno = getattr(exc, "errno", None)
            if isinstance(errno, int):
                print(f"errno: {errno}")
            print("No Gmail content or message identifiers printed.")
            return 1
        print(f"validated_range: [{start}, {stop})")
    print("result: PASS")
    print("note: a clean slice does not rule out cumulative memory or disk failures")
    return 0


def main() -> int:
    args = _parse_args()
    try:
        return diagnose(
            artifact_root=args.artifact_root,
            account_id=args.account_id,
            snapshot_run_id=args.snapshot_run_id,
            email_address=args.email_address,
            start_index=args.start_index,
            stop_index=args.stop_index,
            batch_size=args.batch_size,
        )
    except (OSError, ValueError) as exc:
        print("result: INVALID_INPUT")
        print(f"exception_type: {type(exc).__name__}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
