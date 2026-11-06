"""Bounded diagnostic must read legacy RAW without network or filesystem writes."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.control import MessageDiscoveryQueue
from gmail_ingestion.persistence.raw_message_writer import RawMessageWriter
from scripts.diagnose_legacy_baseline import diagnose

ACCOUNT_ID = "acct_0123456789abcdef01234567"
RUN_ID = UUID("00000000-0000-0000-0000-000000000981")
T0 = datetime(2026, 10, 25, 21, tzinfo=UTC)


def _seed(root: Path, *, broken: bool = False) -> LocalArtifactStore:
    store = LocalArtifactStore(root)
    queue = MessageDiscoveryQueue(store, RUN_ID, ACCOUNT_ID, "100")
    queue.merge_page(
        [
            {"id": "m1", "threadId": "t1"},
            {"id": "m2", "threadId": "t2"},
        ],
        page_index=0,
        discovered_at=T0,
    )
    queue.mark_discovery_complete()
    writer = RawMessageWriter(store)
    for message_id, thread_id in (("m1", "t1"), ("m2", "t2")):
        if broken and message_id == "m2":
            continue
        body = base64.urlsafe_b64encode(b"hello").decode()
        writer.write(
            RUN_ID,
            message_id,
            {
                "id": message_id,
                "threadId": thread_id,
                "historyId": "100",
                "internalDate": "1000",
                "snippet": "test",
                "sizeEstimate": 5,
                "labelIds": ["INBOX"],
                "payload": {
                    "partId": "",
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "From", "value": "sender@example.com"},
                        {"name": "To", "value": "user@example.com"},
                        {"name": "Subject", "value": "test"},
                    ],
                    "body": {"size": 5, "data": body},
                },
            },
        )
    queue.mark_fetched("m1", attempts=1)
    queue.mark_fetched("m2", attempts=1)
    queue.persist()
    return store


def test_diagnostic_reads_bounded_range_without_writes(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    _seed(root)
    before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))

    result = diagnose(
        artifact_root=str(root),
        account_id=ACCOUNT_ID,
        snapshot_run_id=RUN_ID,
        email_address="user@example.com",
        start_index=0,
        stop_index=2,
        batch_size=1,
    )
    after = sorted(str(path.relative_to(root)) for path in root.rglob("*"))

    assert result == 0
    assert after == before


def test_diagnostic_reports_missing_raw_without_identifiers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "artifacts"
    _seed(root, broken=True)

    result = diagnose(
        artifact_root=str(root),
        account_id=ACCOUNT_ID,
        snapshot_run_id=RUN_ID,
        email_address="user@example.com",
        start_index=0,
        stop_index=2,
        batch_size=1,
    )

    assert result == 1
    output = capsys.readouterr().out
    assert "failing_range: [1, 2)" in output
    assert "stage: read-raw" in output
    assert "result: FAIL" in output
    assert "m2" not in output
    assert "sender@example.com" not in output
