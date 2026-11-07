"""Disk-backed RC8 reducer parity, bounded writes, immutable failure tests."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pyarrow.parquet as pq
import pytest

from gmail_ingestion.application.rc8_current_reconciliation import reconcile_current_rows
from gmail_ingestion.application.rc8_disk_projection import (
    DiskCurrentProjection,
    RC8DiskProjectionError,
)
from gmail_ingestion.domain import GmailAccount, GmailHistoryEvent, GmailHistoryEventType
from gmail_ingestion.normalization import normalize_snapshot
from gmail_ingestion.projection.digest import logical_projection_digest
from gmail_ingestion.projection.message_delta import (
    CURRENT_PROJECTION_TABLES,
    MESSAGE_PROJECTION_TABLES,
    PreparedMessageDelta,
)

ACCOUNT = "acct_0123456789abcdef01234567"
BASE = UUID("00000000-0000-0000-0000-000000009901")
NOW = datetime(2026, 11, 7, tzinfo=UTC)
GMAIL_ACCOUNT = GmailAccount(
    account_id=ACCOUNT, email_address="user@example.com",
    messages_total=4, threads_total=4, history_id="100",
)


def _message(mid: str, *, history: str = "100", thread: str = "") -> dict[str, Any]:
    body = base64.urlsafe_b64encode(mid.encode()).decode().rstrip("=")
    return {
        "id": mid, "threadId": thread or "thread-" + mid,
        "historyId": history, "internalDate": "10000",
        "labelIds": ["INBOX"], "snippet": "snippet-" + mid,
        "sizeEstimate": 100,
        "payload": {
            "partId": "", "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Sender <sender@example.com>"},
                {"name": "To", "value": "Receiver <user@example.com>"},
                {"name": "Subject", "value": mid},
            ],
            "body": {"size": len(mid), "data": body},
        },
    }


def _norm(mid: str, *, history: str = "100", thread: str = "") -> dict[str, list[dict[str, Any]]]:
    normalized = normalize_snapshot(
        GMAIL_ACCOUNT, (), (_message(mid, history=history, thread=thread),),
        ingested_at=NOW, dataset_version="synthetic", source_connector_version="test",
    )
    return {name: normalized.rows(name) for name in MESSAGE_PROJECTION_TABLES}


def _event(
    mid: str, history: str, typ: GmailHistoryEventType,
    *, label: str | None = None, thread: str = "",
) -> GmailHistoryEvent:
    return GmailHistoryEvent(
        event_id=f"{history}/{mid}/{typ.value}", account_id=ACCOUNT,
        history_id=history, event_type=typ,
        message_id=mid, thread_id=thread or "thread-" + mid,
        label_id=label, event_ordinal=0, source_page_token=None,
        observed_at=NOW, run_id=BASE,
    ).mark_processed()


class _Stream:
    def __init__(self, tables: dict[str, list[dict[str, Any]]]) -> None:
        self.tables = tables
        self.used_tables: list[str] = []

    def iter_verified_segmented_table(
        self, run_id: UUID, name: str, *, max_rows_per_batch: int,
    ) -> Any:
        assert run_id == BASE
        self.used_tables.append(name)
        assert max_rows_per_batch == 2
        for i in range(0, len(self.tables[name]), max_rows_per_batch):
            yield self.tables[name][i:i + max_rows_per_batch]


def test_disk_reducer_matches_eager_semantics_and_stable_digest(tmp_path: Path) -> None:
    source = {"base-a", "new-c", "recovery-d"}
    original = [_norm("base-a", thread="common"), _norm("base-b")]
    baseline = {
        name: [row for bundle in original for row in bundle[name]]
        for name in MESSAGE_PROJECTION_TABLES
    }
    recovery = {"recovery-d": _norm("recovery-d", thread="common")}
    events = [
        _event("new-c", "101", GmailHistoryEventType.MESSAGE_ADDED),
        _event("base-a", "102", GmailHistoryEventType.LABEL_ADDED,
               label="STARRED", thread="common"),
        _event("base-b", "103", GmailHistoryEventType.MESSAGE_DELETED),
        _event("base-a", "104", GmailHistoryEventType.LABEL_REMOVED,
               label="INBOX", thread="common"),
    ]
    prepared = [
        PreparedMessageDelta(event=events[0], normalized_rows=_norm("new-c", history="101")),
        *[PreparedMessageDelta(event=event) for event in events[1:]],
    ]
    eager, _ = reconcile_current_rows(
        account_id=ACCOUNT, baseline_tables=baseline,
        prepared_deltas=prepared, source_message_ids=source,
        current_recovery_rows=recovery, committed_events=events,
    )
    scratch = tmp_path / "private.sqlite3"
    disk = DiskCurrentProjection(scratch, ACCOUNT)
    try:
        streamed = _Stream(baseline)
        disk.ingest_baseline(streamed, BASE, max_rows_per_batch=2)  # type: ignore[arg-type]
        assert streamed.used_tables == list(MESSAGE_PROJECTION_TABLES)
        disk.apply_deltas(tuple(reversed(prepared)))
        result = disk.reconcile_scope(source, recovery)
        assert result["source_only_recovered_count"] == 1
        disk.finalize(source, events)
        assert disk.row_counts() == {name: len(eager[name]) for name in CURRENT_PROJECTION_TABLES}
        assert disk.logical_digest() == logical_projection_digest(
            eager, table_names=CURRENT_PROJECTION_TABLES
        )
        directory = tmp_path / "candidate"
        digests = disk.write_parquet(directory, batch_size=2)
        assert set(digests) == set(CURRENT_PROJECTION_TABLES)
        for name in CURRENT_PROJECTION_TABLES:
            assert pq.read_table(directory / (name + ".parquet")).num_rows == len(eager[name])
    finally:
        disk.close()


def test_disk_projection_prevents_scratch_overwrite_and_cross_account(tmp_path: Path) -> None:
    path = tmp_path / "private.sqlite3"
    disk = DiskCurrentProjection(path, ACCOUNT)
    try:
        with pytest.raises(RC8DiskProjectionError, match="Cross-account"):
            disk.replace_bundle("bad", {"messages": [
                {"message_id": "bad", "account_id": "other", "thread_id": "thread-bad"}
            ]})
        with pytest.raises(RC8DiskProjectionError, match="Source-only"):
            disk.reconcile_scope({"not-captured"}, {})
        with pytest.raises(RC8DiskProjectionError, match="incomplete"):
            disk.replace_bundle("bad", {"messages": []})
    finally:
        disk.close()
    with pytest.raises(RC8DiskProjectionError, match="already exists"):
        DiskCurrentProjection(path, ACCOUNT)


def test_disk_projection_rejects_spam_without_forged_delete(tmp_path: Path) -> None:
    disk = DiskCurrentProjection(tmp_path / "spam.sqlite3", ACCOUNT)
    try:
        bundle = _norm("spam-item")
        bundle["message_labels"][0]["label_id"] = "SPAM"
        disk.replace_bundle("spam-item", bundle)
        with pytest.raises(RC8DiskProjectionError, match="Spam/Trash"):
            disk.finalize({"spam-item"}, [])
    finally:
        disk.close()
