"""Synthetic end-to-end RC8: segmented baseline -> disk -> Parquet -> audit -> Gmail."""

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
from gmail_ingestion.persistence import LocalProjectionStore, RawMessageWriter
from gmail_ingestion.persistence.resumable_baseline_writer import (
    ResumableProjectionBaselineWriter,
)
from gmail_ingestion.projection import ProjectionDeltaBatch
from gmail_ingestion.projection.message_delta import MESSAGE_PROJECTION_TABLES

ACCOUNT = "acct_0123456789abcdef01234567"
BASE = UUID("00000000-0000-0000-0000-000000009501")
DELTA = UUID("00000000-0000-0000-0000-000000009502")
RECOVERY = UUID("00000000-0000-0000-0000-000000009503")
NOW = datetime(2026, 11, 7, tzinfo=UTC)


def _payload(mid: str, history: str) -> dict[str, Any]:
    data = base64.urlsafe_b64encode(mid.encode()).decode().rstrip("=")
    return {
        "id": mid, "threadId": "thread-" + mid, "historyId": history,
        "internalDate": "10000", "snippet": mid,
        "sizeEstimate": 80, "labelIds": ["INBOX"],
        "payload": {
            "partId": "", "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "sender@example.com"},
                {"name": "To", "value": "user@example.com"},
                {"name": "Subject", "value": "Synthetic"},
            ],
            "body": {"size": len(mid), "data": data},
        },
    }


def test_partitioned_end_to_end_with_source_sample(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    script = importlib.import_module("materialize_rc8_partitioned_candidate")
    audit = importlib.import_module("audit_rc8_partitioned_candidate")
    parity = importlib.import_module("verify_rc8_source_parity")
    exhaustive = importlib.import_module("verify_rc8_full_source_parity")
    artifacts = tmp_path / "artifacts"
    projection_root = tmp_path / "isolated"
    projection_root.mkdir()
    store = LocalArtifactStore(artifacts)
    account = GmailAccount(
        account_id=ACCOUNT, email_address="user@example.com",
        messages_total=1, threads_total=1, history_id="100",
    )
    baseline_msg = _payload("base", "100")
    recovered_msg = _payload("missing", "105")
    normalized = normalize_snapshot(
        account, [{"id": "INBOX", "name": "INBOX", "type": "system"}],
        [baseline_msg], ingested_at=NOW,
        dataset_version="baseline", source_connector_version="0.5.0rc8",
    )
    ResumableProjectionBaselineWriter(store).write(
        run_id=BASE, account_id=ACCOUNT, history_id="100",
        account_row=normalized.rows("accounts")[0], batch_size=1,
        total_messages=1,
        batch_loader=lambda start, stop: {
            name: normalized.rows(name) for name in MESSAGE_PROJECTION_TABLES
        },
        dataset_version="baseline", source_connector_version="0.5.0rc8",
    )

    event = GmailHistoryEvent(
        event_id="event-101", account_id=ACCOUNT, history_id="101",
        event_type=GmailHistoryEventType.MESSAGE_ADDED,
        message_id="missing", thread_id="thread-missing",
        label_id=None, event_ordinal=0, source_page_token=None,
        observed_at=NOW, run_id=DELTA,
    ).mark_processed()
    batch = ProjectionDeltaBatch(
        account_id=ACCOUNT, run_id=DELTA,
        input_history_id="100", output_history_id="110",
        events=(event,),
    )

    class _Batches:
        def __init__(self, _: Any) -> None:
            pass

        def read_many(self, _: Any) -> tuple[ProjectionDeltaBatch, ...]:
            return (batch,)

    monkeypatch.setattr(script, "LocalProjectionBatchReader", _Batches)
    plan = {
        "candidate_id": "rc8_" + "1" * 32,
        "account_id": ACCOUNT,
        "source_history_id": "110",
        "source_id_set_sha256": "a" * 64,
        "exception_ledger_sha256": "b" * 64,
        "private_recovery_required_ids": ["missing"],
    }
    source_ids = {"base", "missing"}
    source = {
        "account_id": ACCOUNT, "history_end": "110",
        "id_set_sha256": "a" * 64,
    }
    monkeypatch.setattr(script, "_source", lambda _: (source_ids, source))
    monkeypatch.setattr(script, "build_candidate_plan", lambda **_: plan)
    monkeypatch.setattr(script, "require_resource_headroom", lambda *_, **__: {
        "free_disk_gib": 64.0, "available_ram_gib": 48.0
    })

    def sha(data: Any) -> str:
        return hashlib.sha256(
            json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()

    raw = RawMessageWriter(store, job_id="gmail.rc8_source_evidence")
    raw.write(RECOVERY, "missing", recovered_msg)
    source_manifest = {
        "schema_version": 1, "status": "COMPLETED",
        "job_id": "gmail.rc8_source_evidence",
        "run_id": str(RECOVERY), "candidate_id": plan["candidate_id"],
        "account_id": ACCOUNT, "source_history_id": "110",
        "source_id_set_sha256": plan["source_id_set_sha256"],
        "exception_ledger_sha256": plan["exception_ledger_sha256"],
        "recovery_message_ids": ["missing"], "recovery_message_count": 1,
        "content_sha256": {"missing": sha(recovered_msg)},
    }
    manifest_path = (
        store.run_root("gmail.rc8_source_evidence", RECOVERY)
        / "source_manifest.json"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(source_manifest), encoding="utf-8")
    result = script.build_partitioned_candidate(
        artifact_root=artifacts, projection_root=projection_root,
        account_id=ACCOUNT, baseline_run_id=BASE,
        delta_run_ids=(DELTA,), source_inventory=tmp_path / "source.json",
        recovery_run_id=RECOVERY, batch_size=2,
        min_free_gib=1, min_available_ram_gib=1,
    )
    assert result["source_message_count"] == 2
    assert result["deterministic_replay_equal"] is True
    assert result["row_counts"]["messages"] == 2
    assert result["diagnostics"]["current_only_raw_substitutions"] == 1
    assert result["current_pointer_promoted"] is False
    assert not (projection_root / ("work-" + result["projection_id"])).exists()
    assert LocalProjectionStore(projection_root).load_current(ACCOUNT) is None

    monkeypatch.setattr(audit, "_source", lambda _: (source_ids, source))
    verified = audit.audit_candidate(
        projection_root=projection_root, account_id=ACCOUNT,
        projection_id=result["projection_id"],
        source_inventory=tmp_path / "source.json",
    )
    assert verified["source_id_set_equal"] is True
    assert verified["logical_digest_verified"] is True
    assert verified["stable_release_qualified"] is False

    monkeypatch.setattr(parity, "_source", lambda _: (source_ids, source))

    class _Api:
        class Messages:
            def get_message(self, mid: str, *, format: str) -> dict[str, Any]:
                assert format == "full"
                return baseline_msg if mid == "base" else recovered_msg

        class History:
            def list_history(self, *, start_history_id: str, **_: Any) -> dict[str, Any]:
                assert start_history_id == "110"
                return {"historyId": "110", "history": []}

        messages = Messages()
        history = History()

    live = parity.validate_sample(
        account_id=ACCOUNT, artifact_root=artifacts, projection_root=projection_root,
        baseline_run_id=BASE, projection_id=result["projection_id"],
        scoped_inventory=tmp_path / "source.json",
        client=_Api(), sample_size=2,
    )
    assert live["sample_verified"] == 2
    assert live["current_pointer_promoted"] is False

    monkeypatch.setattr(exhaustive, "_source", lambda _: (source_ids, source))
    full = exhaustive.validate_full_source(
        client=_Api(), account_id=ACCOUNT, artifact_root=artifacts,
        projection_root=projection_root, baseline_run_id=BASE,
        projection_id=result["projection_id"],
        scoped_inventory=tmp_path / "source.json",
        max_messages=2, batch_size=1, max_history_pages=2,
    )
    assert full["full_messages_verified"] == 2
    assert full["full_mailbox_verified"] is True
    assert full["stable_release_qualified"] is False

    with pytest.raises(exhaustive.RC8FullSourceParityError, match="cap"):
        exhaustive.validate_full_source(
            client=_Api(), account_id=ACCOUNT, artifact_root=artifacts,
            projection_root=projection_root, baseline_run_id=BASE,
            projection_id=result["projection_id"],
            scoped_inventory=tmp_path / "source.json",
            max_messages=1,
        )

    class _DriftApi(_Api):
        class Messages(_Api.Messages):
            def get_message(self, mid: str, *, format: str) -> dict[str, Any]:
                payload = super().get_message(mid, format=format)
                return dict(payload, historyId="111")

        messages = Messages()

    with pytest.raises(exhaustive.RC8FullSourceParityError, match="drifted"):
        exhaustive.validate_full_source(
            client=_DriftApi(), account_id=ACCOUNT, artifact_root=artifacts,
            projection_root=projection_root, baseline_run_id=BASE,
            projection_id=result["projection_id"],
            scoped_inventory=tmp_path / "source.json",
            max_messages=2,
        )

    # Inject a non-deterministic second pass: Parquet can exist, but no
    # complete manifest or authoritative pointer is ever published.
    failure_root = tmp_path / "isolated-failure"
    failure_root.mkdir()
    digest_original = script.DiskCurrentProjection.logical_digest
    digest_calls = 0

    def mismatched_replay(self: Any) -> str:
        nonlocal digest_calls
        digest_calls += 1
        actual = digest_original(self)
        return "0" * 64 if digest_calls == 2 else actual

    monkeypatch.setattr(script.DiskCurrentProjection, "logical_digest", mismatched_replay)
    with pytest.raises(script.RC8PartitionedBuildError, match="Independent replay"):
        script.build_partitioned_candidate(
            artifact_root=artifacts, projection_root=failure_root,
            account_id=ACCOUNT, baseline_run_id=BASE,
            delta_run_ids=(DELTA,), source_inventory=tmp_path / "source.json",
            recovery_run_id=RECOVERY, batch_size=2,
            min_free_gib=1, min_available_ram_gib=1,
        )
    assert digest_calls == 2
    assert list(failure_root.rglob("*.parquet"))
    assert not list(failure_root.rglob("projection-data.json"))
    assert not list(failure_root.rglob("rc8-provenance.json"))
    assert LocalProjectionStore(failure_root).load_current(ACCOUNT) is None

    # Tampering with a previously completed candidate must fail offline audit.
    parquet = (
        projection_root / "accounts" / ACCOUNT / "projections"
        / "candidate_data" / result["projection_id"]
        / "normalized" / "messages.parquet"
    )
    with parquet.open("ab") as handle:
        handle.write(b"tampered")
    with pytest.raises(audit.RC8CandidateAuditError, match="integrity"):
        audit.audit_candidate(
            projection_root=projection_root, account_id=ACCOUNT,
            projection_id=result["projection_id"],
            source_inventory=tmp_path / "source.json",
        )
