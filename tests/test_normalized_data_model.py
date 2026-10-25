from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pyarrow.parquet as pq
import pytest
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.domain import GmailAccount
from gmail_ingestion.normalization import normalize_snapshot
from gmail_ingestion.persistence import NormalizedParquetWriter
from gmail_ingestion.quality import NormalizedQualityError, validate_normalized_tables
from gmail_ingestion.schemas import NORMALIZED_SCHEMA_VERSION, TABLE_CONTRACTS


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _fixture() -> tuple[GmailAccount, list[dict[str, object]], list[dict[str, object]]]:
    account = GmailAccount(
        "acct_0123456789abcdef01234567",
        "user@example.com",
        2,
        1,
        "502",
    )
    labels: list[dict[str, object]] = [
        {"id": "INBOX", "name": "INBOX", "type": "system"},
        {"id": "Label_1", "name": "Clients", "type": "user"},
    ]
    messages: list[dict[str, object]] = [
        {
            "id": "m1",
            "threadId": "t1",
            "historyId": "501",
            "internalDate": "1000",
            "snippet": "hello",
            "sizeEstimate": 100,
            "labelIds": ["INBOX"],
            "payload": {
                "partId": "",
                "mimeType": "multipart/mixed",
                "headers": [
                    {"name": "From", "value": "Alice <alice@example.com>"},
                    {"name": "To", "value": "User <user@example.com>"},
                    {"name": "Subject", "value": "First"},
                    {"name": "Message-ID", "value": "<m1@example.com>"},
                ],
                "body": {"size": 0},
                "parts": [
                    {
                        "partId": "0",
                        "mimeType": "text/plain",
                        "headers": [
                            {"name": "Content-Type", "value": "text/plain; charset=utf-8"}
                        ],
                        "body": {"size": 5, "data": _b64("hello")},
                    },
                    {
                        "partId": "1",
                        "mimeType": "application/pdf",
                        "filename": "invoice.pdf",
                        "headers": [
                            {"name": "Content-Disposition", "value": "attachment; filename=invoice.pdf"}
                        ],
                        "body": {"size": 3, "attachmentId": "a1"},
                    },
                ],
            },
        },
        {
            "id": "m2",
            "threadId": "t1",
            "historyId": "502",
            "internalDate": "2000",
            "snippet": "reply",
            "sizeEstimate": 80,
            "labelIds": ["INBOX", "Label_1"],
            "payload": {
                "partId": "",
                "mimeType": "text/plain",
                "headers": [
                    {"name": "From", "value": "User <user@example.com>"},
                    {"name": "To", "value": "Bob <bob@example.net>"},
                    {"name": "Subject", "value": "Re: First"},
                    {"name": "In-Reply-To", "value": "<m1@example.com>"},
                ],
                "body": {"size": 5, "data": _b64("reply")},
            },
        },
    ]
    return account, labels, messages


def test_normalized_schema_v1_is_deterministic_and_relational() -> None:
    account, labels, messages = _fixture()
    timestamp = datetime(2026, 10, 25, 8, 30, tzinfo=UTC)

    first = normalize_snapshot(
        account,
        labels,
        messages,
        ingested_at=timestamp,
        dataset_version="run-test",
        source_connector_version="0.2.0a1",
    )
    second = normalize_snapshot(
        account,
        labels,
        messages,
        ingested_at=timestamp,
        dataset_version="run-test",
        source_connector_version="0.2.0a1",
    )

    assert set(first.tables) == set(TABLE_CONTRACTS)
    assert {name: first.rows(name) for name in first.tables} == {
        name: second.rows(name) for name in second.tables
    }
    assert first.rows("messages")[0]["normalized_schema_version"] == NORMALIZED_SCHEMA_VERSION
    assert first.rows("messages")[0]["text_body"] == "hello"
    assert first.rows("messages")[0]["attachment_count"] == 1
    assert len(first.rows("threads")) == 1
    assert first.rows("threads")[0]["message_count"] == 2
    assert first.rows("threads")[0]["first_message_id"] == "m1"
    assert first.rows("threads")[0]["last_message_id"] == "m2"
    assert len(first.rows("message_labels")) == 3
    assert {row["recipient_role"] for row in first.rows("message_recipients")} == {"FROM", "TO"}

    report = validate_normalized_tables(
        {name: dataset.to_rows() for name, dataset in first.tables.items()}
    )
    assert report.normalized_schema_version == "1"
    assert report.row_counts["messages"] == 2
    assert report.row_counts["attachments"] == 1


def test_normalized_parquet_round_trip_is_logically_reproducible(tmp_path: Path) -> None:
    account, labels, messages = _fixture()
    timestamp = datetime(2026, 10, 25, 8, 30, tzinfo=UTC)
    normalized = normalize_snapshot(
        account,
        labels,
        messages,
        ingested_at=timestamp,
        dataset_version="run-test",
        source_connector_version="0.2.0a1",
    )
    store = LocalArtifactStore(tmp_path)
    writer = NormalizedParquetWriter(store)

    first = writer.write(
        UUID("00000000-0000-0000-0000-000000000501"),
        normalized.tables,
    )
    second = writer.write(
        UUID("00000000-0000-0000-0000-000000000502"),
        normalized.tables,
    )

    assert first.schema_path.is_file()
    assert first.quality_path.is_file()
    assert set(first.table_paths) == set(TABLE_CONTRACTS)
    for name in TABLE_CONTRACTS:
        first_rows = pq.read_table(first.table_paths[name]).to_pylist()
        second_rows = pq.read_table(second.table_paths[name]).to_pylist()
        assert first_rows == second_rows


def test_quality_rejects_broken_message_thread_reference() -> None:
    account, labels, messages = _fixture()
    normalized = normalize_snapshot(
        account,
        labels,
        messages,
        ingested_at=datetime(2026, 10, 25, 8, 30, tzinfo=UTC),
        dataset_version="run-test",
        source_connector_version="0.2.0a1",
    )
    tables = {name: dataset.to_rows() for name, dataset in normalized.tables.items()}
    tables["messages"][0]["thread_id"] = "missing-thread"

    with pytest.raises(NormalizedQualityError, match="messages -> threads"):
        validate_normalized_tables(tables)
