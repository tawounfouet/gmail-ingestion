"""Bounded diagnostic must read legacy RAW without network or filesystem writes."""

from __future__ import annotations

import base64
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.control import MessageDiscoveryQueue
from gmail_ingestion.persistence.raw_message_writer import RawMessageWriter

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/diagnose_legacy_baseline.py"

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


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--artifact-root",
            str(root),
            "--account-id",
            ACCOUNT_ID,
            "--snapshot-run-id",
            str(RUN_ID),
            "--email-address",
            "user@example.com",
            "--start-index",
            "0",
            "--stop-index",
            "2",
            "--batch-size",
            "1",
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_diagnostic_reads_bounded_range_without_writes(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    _seed(root)
    before = sorted(str(path.relative_to(root)) for path in root.rglob("*"))
    completed = _run(root)
    after = sorted(str(path.relative_to(root)) for path in root.rglob("*"))

    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "result: PASS" in completed.stdout
    assert after == before


def test_diagnostic_reports_missing_raw_without_identifiers(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    _seed(root, broken=True)
    completed = _run(root)

    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert "failing_range: [1, 2)" in completed.stdout
    assert "stage: read-raw" in completed.stdout
    assert "result: FAIL" in completed.stdout
    assert "m2" not in completed.stdout
    assert "sender@example.com" not in completed.stdout
