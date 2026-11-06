"""Read-only diagnostic tests for historical missing MESSAGE_ADDED RAW artifacts."""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pyarrow as pa
import pyarrow.parquet as pq
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.domain import (
    GmailHistoryEvent,
    GmailHistoryEventType,
    HistoryProcessingStatus,
)
from gmail_ingestion.persistence import (
    HistoryEventWriter,
    IncrementalManifestWriter,
    RawMessageWriter,
)

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/audit_missing_added_raw.py"
ACCOUNT = "acct_0123456789abcdef01234567"
BASE = UUID("00000000-0000-0000-0000-000000000555")
RUN = UUID("00000000-0000-0000-0000-000000000556")
NOW = datetime(2026, 11, 6, tzinfo=UTC)


def _event(index: int, message_id: str, kind: GmailHistoryEventType) -> GmailHistoryEvent:
    return GmailHistoryEvent(
        event_id=f"event-{index}",
        account_id=ACCOUNT,
        history_id=str(100 + index),
        event_type=kind,
        message_id=message_id,
        thread_id=f"thread-{message_id}",
        label_id=None,
        event_ordinal=0,
        source_page_token=None,
        observed_at=NOW,
        run_id=RUN,
        processed_at=NOW,
        processing_status=HistoryProcessingStatus.PROCESSED,
    )


def _seed(root: Path) -> None:
    store = LocalArtifactStore(root)
    baseline_root = store.run_root("gmail.projection_baseline", BASE)
    segment_dir = baseline_root / "segments" / "00000000"
    segment_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.table({"message_id": ["m-baseline"]}), segment_dir / "messages.parquet")
    (baseline_root / "baseline_manifest.json").write_text(
        json.dumps(
            {
                "status": "COMPLETED",
                "layout": "segmented-parquet-v1",
                "account_id": ACCOUNT,
                "source_snapshot_run_id": str(BASE),
                "history_id": "100",
                "segment_count": 1,
            }
        ), encoding="utf-8",
    )
    history = HistoryEventWriter(store)
    events = (
        _event(1, "m-baseline", GmailHistoryEventType.MESSAGE_ADDED),
        _event(2, "m-deleted", GmailHistoryEventType.MESSAGE_ADDED),
        _event(3, "m-unresolved", GmailHistoryEventType.MESSAGE_ADDED),
        _event(4, "m-other-raw", GmailHistoryEventType.MESSAGE_ADDED),
        _event(5, "m-current-raw", GmailHistoryEventType.MESSAGE_ADDED),
        _event(6, "m-deleted", GmailHistoryEventType.MESSAGE_DELETED),
    )
    for event in events:
        history.write(event)
    IncrementalManifestWriter(store).write(
        RUN,
        {
            "status": "COMPLETED",
            "run_id": str(RUN),
            "account_id": ACCOUNT,
            "input_checkpoint": "100",
            "committed_checkpoint": "110",
            "events_persisted": len(events),
        },
    )
    RawMessageWriter(store).write(BASE, "m-other-raw", {"id": "m-other-raw"})
    RawMessageWriter(store).write(RUN, "m-current-raw", {"id": "m-current-raw"})


def test_audit_classifies_historical_missing_additions_read_only(tmp_path: Path) -> None:
    root = tmp_path / "artifacts"
    _seed(root)
    before = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--artifact-root",
            str(root),
            "--account-id",
            ACCOUNT,
            "--baseline-run-id",
            str(BASE),
            "--delta-run-id",
            str(RUN),
            "--example-limit",
            "0",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    after = sorted(str(p.relative_to(root)) for p in root.rglob("*"))
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(completed.stdout)
    assert result["message_add_events"] == 5
    assert result["missing_addition_raw_count"] == 4
    assert result["classifications"] == {
        "baseline_present_needs_review": 1,
        "later_deleted": 1,
        "raw_in_other_run": 1,
        "unresolved_missing": 1,
    }
    assert result["examples"] == []
    assert result["gmail_api_calls"] == 0
    assert result["persistent_writes"] == 0
    assert after == before
    assert "m-unresolved" not in completed.stdout
