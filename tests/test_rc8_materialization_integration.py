"""Synthetic end-to-end RC8 candidate materialization without Gmail API access."""

from __future__ import annotations

import base64
import hashlib
import importlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.domain import GmailAccount, GmailHistoryEvent, GmailHistoryEventType
from gmail_ingestion.normalization import normalize_snapshot
from gmail_ingestion.persistence import (
    LocalProjectionStore,
    NormalizedParquetWriter,
)
from gmail_ingestion.persistence.projection_dataset_store import LocalProjectionDatasetStore
from gmail_ingestion.projection import CURRENT_PROJECTION_TABLES, ProjectionDeltaBatch

ACCOUNT = "acct_0123456789abcdef01234567"
BASE = UUID("00000000-0000-0000-0000-000000009401")
DELTA = UUID("00000000-0000-0000-0000-000000009402")
RECOVERY = UUID("00000000-0000-0000-0000-000000009403")
NOW = datetime(2026, 11, 6, tzinfo=UTC)


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("materialize_rc8_current_candidate")


def _payload(mid: str, *, history_id: str, subject: str) -> dict[str, Any]:
    body = base64.urlsafe_b64encode(subject.encode()).decode().rstrip("=")
    return {
        "id": mid,
        "threadId": "thread-" + mid,
        "historyId": history_id,
        "internalDate": "10000",
        "snippet": subject,
        "sizeEstimate": 12,
        "labelIds": ["INBOX"],
        "payload": {
            "partId": "",
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Sender <sender@example.com>"},
                {"name": "To", "value": "User <user@example.com>"},
                {"name": "Subject", "value": subject},
            ],
            "body": {"size": len(subject), "data": body},
        },
    }


def test_end_to_end_candidate_is_isolated_and_deterministically_replayed(
    module: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact_root = tmp_path / "artifacts"
    projection_root = tmp_path / "rc8-control"
    store = LocalArtifactStore(artifact_root)
    account = GmailAccount(
        account_id=ACCOUNT, email_address="user@example.com",
        messages_total=1, threads_total=1, history_id="100",
    )
    normalized = normalize_snapshot(
        account, [{"id": "INBOX", "name": "INBOX", "type": "system"}],
        [_payload("base", history_id="100", subject="existing")],
        ingested_at=NOW,
        dataset_version="baseline",
        source_connector_version="0.5.0rc7",
    )
    NormalizedParquetWriter(store).write(BASE, normalized.tables)
    recovery_payloads = {
        "missing-current": _payload(
            "missing-current", history_id="108", subject="from current source"
        ),
        "moved-out-of-trash": _payload(
            "moved-out-of-trash", history_id="108", subject="moved to inbox"
        ),
    }
    event = GmailHistoryEvent(
        event_id="event-101", account_id=ACCOUNT, history_id="101",
        event_type=GmailHistoryEventType.MESSAGE_ADDED,
        message_id="missing-current", thread_id="thread-missing-current",
        label_id=None, event_ordinal=0, source_page_token=None,
        observed_at=NOW, run_id=DELTA,
    ).mark_processed()
    batch = ProjectionDeltaBatch(
        account_id=ACCOUNT, run_id=DELTA,
        input_history_id="100", output_history_id="110", events=(event,),
    )
    candidate = {
        "candidate_id": "rc8_" + "1" * 32,
        "account_id": ACCOUNT,
        "source_history_id": "110",
        "source_id_set_sha256": "a" * 64,
        "exception_ledger_sha256": "b" * 64,
        "private_recovery_required_ids": ["missing-current", "moved-out-of-trash"],
    }
    def _sha(payload: Any) -> str:
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
    source_manifest = {
        "schema_version": 1, "status": "COMPLETED",
        "job_id": "gmail.rc8_source_evidence",
        "run_id": str(RECOVERY),
        "candidate_id": candidate["candidate_id"],
        "account_id": ACCOUNT,
        "source_history_id": "110",
        "source_id_set_sha256": candidate["source_id_set_sha256"],
        "exception_ledger_sha256": candidate["exception_ledger_sha256"],
        "recovery_message_count": 2,
        "recovery_message_ids": ["missing-current", "moved-out-of-trash"],
        "content_sha256": {
            mid: _sha(payload) for mid, payload in recovery_payloads.items()
        },
    }
    evidence_root = store.run_root("gmail.rc8_source_evidence", RECOVERY)
    evidence_root.mkdir(parents=True, exist_ok=True)
    (evidence_root / "source_manifest.json").write_text(
        json.dumps(source_manifest), encoding="utf-8"
    )
    for mid, payload in recovery_payloads.items():
        module.RawMessageWriter(
            store, job_id="gmail.rc8_source_evidence"
        ).write(RECOVERY, mid, payload)

    class _Reader:
        def __init__(self, _: Any) -> None:
            pass

        def read_many(self, _: Any) -> tuple[ProjectionDeltaBatch, ...]:
            return (batch,)

    source_ids = {"base", *recovery_payloads}
    monkeypatch.setattr(module, "LocalProjectionBatchReader", _Reader)
    monkeypatch.setattr(module, "_source", lambda _: (
        source_ids, {"id_set_sha256": candidate["source_id_set_sha256"]}
    ))
    monkeypatch.setattr(module, "build_candidate_plan", lambda **_: candidate)
    monkeypatch.setattr(module, "require_resource_headroom", lambda *_, **__: {
        "free_disk_gib": 64.0, "available_ram_gib": 48.0
    })

    result = module.build_current_candidate(
        artifact_root=artifact_root, projection_root=projection_root,
        account_id=ACCOUNT, baseline_run_id=BASE,
        delta_run_ids=(DELTA,), source_inventory=tmp_path / "private.json",
        recovery_run_id=RECOVERY, min_free_gib=1, min_available_ram_gib=1,
    )
    assert result["source_message_count"] == 3
    assert result["deterministic_replay_equal"] is True
    assert result["row_counts"]["messages"] == 3
    assert result["stable_release_qualified"] is False
    projections = LocalProjectionStore(projection_root)
    assert projections.load_current(ACCOUNT) is None
    rows = LocalProjectionDatasetStore(projections).read_candidate(
        ACCOUNT, result["projection_id"], table_names=CURRENT_PROJECTION_TABLES
    )
    assert {row["message_id"] for row in rows["messages"]} == source_ids
    assert len(rows["threads"]) == 3
    path = projections.candidate_data_dir(ACCOUNT, result["projection_id"]) / "rc8-provenance.json"
    proof = json.loads(path.read_text(encoding="utf-8"))
    assert proof["status"] == "CANDIDATE_DATASET_NOT_SOURCE_QUALIFIED"
    assert proof["current_pointer_promoted"] is False
    assert "missing-current" not in path.read_text(encoding="utf-8")


    verifier = importlib.import_module("verify_rc8_source_parity")
    monkeypatch.setattr(verifier, "_source", lambda _: (
        source_ids, {
            "account_id": ACCOUNT,
            "history_end": "110",
            "id_set_sha256": candidate["source_id_set_sha256"],
        },
    ))

    class SourceApi:
        class Messages:
            @staticmethod
            def get_message(mid: str, *, format: str) -> dict[str, Any]:
                assert format == "full"
                if mid == "base":
                    return _payload(mid, history_id="100", subject="existing")
                return recovery_payloads[mid]

        class History:
            @staticmethod
            def list_history(*, start_history_id: str, **kwargs: Any) -> dict[str, Any]:
                assert start_history_id == "110"
                return {"historyId": "110", "history": []}

        messages = Messages()
        history = History()

    parity = verifier.validate_sample(
        account_id=ACCOUNT,
        artifact_root=artifact_root,
        projection_root=projection_root,
        baseline_run_id=BASE,
        projection_id=result["projection_id"],
        scoped_inventory=tmp_path / "private.json",
        client=SourceApi(),
        sample_size=3,
    )
    assert parity["sample_verified"] == 3
    assert parity["table_count_verified"] == 6
    assert parity["parquet_sha256_verified"] is True
    assert parity["stable_release_qualified"] is False
