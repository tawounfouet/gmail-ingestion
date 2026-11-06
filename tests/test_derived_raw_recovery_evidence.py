"""Test isolated, bounded recovery evidence without any live Gmail credentials."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.api import GmailApiError, GmailMessageNotFoundError
from gmail_ingestion.persistence.raw_message_writer import RawMessageWriter

RUN = UUID("00000000-0000-0000-0000-000000002020")
BASE = UUID("00000000-0000-0000-0000-000000002021")
DELTA = UUID("00000000-0000-0000-0000-000000002022")
ACCOUNT = "acct_0123456789abcdef01234567"
TARGET = "15694719"


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("capture_missing_added_raw_recovery")


class _Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.mode = "normal"

    def get_message(self, message_id: str, *, format: str = "full") -> Mapping[str, Any]:
        self.calls.append((message_id, format))
        if message_id == "gone":
            raise GmailMessageNotFoundError("404")
        if message_id == "broken":
            raise GmailApiError("503")
        if self.mode == "wrong-id":
            return {"id": "wrong", "historyId": "150"}
        if self.mode == "oversize":
            return {"id": message_id, "historyId": "160", "snippet": "x" * 200}
        return {
            "id": message_id,
            "threadId": "thread",
            "historyId": "15694718" if message_id == "before" else "15900000",
            "labelIds": ["INBOX"],
            "snippet": "private metadata",
        }


def _invoke(module: Any, tmp_path: Path, client: Any) -> dict[str, object]:
    return module.capture_evidence(
        artifact_root=tmp_path / "artifacts",
        account_id=ACCOUNT,
        baseline_run_id=BASE,
        delta_run_ids=(DELTA,),
        recovery_run_id=RUN,
        client=client,
        max_messages=4,
        max_message_bytes=1000,
        max_total_bytes=5000,
    )


def test_capture_writes_only_derived_raw_and_idempotent_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any
) -> None:
    monkeypatch.setattr(
        module,
        "unresolved_ids",
        lambda *_args: ({"before", "after", "gone"}, 3, TARGET),
    )
    client = _Client()
    result = _invoke(module, tmp_path, client)
    assert result["status"] == "COMPLETE"
    assert result["captured_current_full"] == 2
    assert result["not_found_404_now"] == 1
    assert result["current_message_history_le_target"] == 1
    assert result["current_message_history_gt_target"] == 1
    assert result["historical_event_mutations"] == 0
    assert result["history_checkpoint_mutations"] == 0
    assert result["manifest_reused"] is False
    assert "before" not in str(result)
    assert "after" not in str(result)
    assert client.calls == [("after", "full"), ("before", "full"), ("gone", "full")]

    store = LocalArtifactStore(tmp_path / "artifacts")
    writer = RawMessageWriter(store, job_id="gmail.recovery_evidence")
    assert writer.exists(RUN, "before")
    assert writer.exists(RUN, "after")
    assert not writer.exists(RUN, "gone")
    assert not (store.run_root("gmail.history", DELTA) / "history_events").exists()
    assert not (store.run_root("gmail.messages", DELTA) / "raw").exists()

    before = sorted(str(p) for p in (tmp_path / "artifacts").rglob("*"))
    reused = _invoke(module, tmp_path, client)
    after = sorted(str(p) for p in (tmp_path / "artifacts").rglob("*"))
    assert reused["manifest_reused"] is True
    assert reused["captured_current_full"] == 2
    assert before == after
    assert len(client.calls) == 3


def test_capture_refuses_non404_and_leaves_no_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any
) -> None:
    monkeypatch.setattr(
        module, "unresolved_ids", lambda *_args: ({"broken"}, 1, TARGET)
    )
    with pytest.raises(module.RecoveryEvidenceError, match="stage=fetch-live-full"):
        _invoke(module, tmp_path, _Client())
    store = LocalArtifactStore(tmp_path / "artifacts")
    assert not (
        store.run_root("gmail.recovery_evidence", RUN) / "recovery_manifest.json"
    ).exists()


def test_capture_refuses_identity_mismatch_and_excessive_sizes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any
) -> None:
    monkeypatch.setattr(
        module, "unresolved_ids", lambda *_args: ({"before"}, 1, TARGET)
    )
    client = _Client()
    client.mode = "wrong-id"
    with pytest.raises(module.RecoveryEvidenceError, match="identity mismatch"):
        _invoke(module, tmp_path, client)
    client.mode = "oversize"
    with pytest.raises(module.RecoveryEvidenceError, match="per-message cap"):
        module.capture_evidence(
            artifact_root=tmp_path / "artifacts",
            account_id=ACCOUNT,
            baseline_run_id=BASE,
            delta_run_ids=(DELTA,),
            recovery_run_id=RUN,
            client=client,
            max_messages=4,
            max_message_bytes=25,
            max_total_bytes=5000,
        )


def test_capture_refuses_request_count_limit_without_api_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any
) -> None:
    monkeypatch.setattr(
        module, "unresolved_ids", lambda *_args: ({"before", "after"}, 2, TARGET)
    )
    client = _Client()
    with pytest.raises(module.RecoveryEvidenceError, match="request limit"):
        module.capture_evidence(
            artifact_root=tmp_path / "artifacts",
            account_id=ACCOUNT,
            baseline_run_id=BASE,
            delta_run_ids=(DELTA,),
            recovery_run_id=RUN,
            client=client,
            max_messages=1,
        )
    assert client.calls == []


def test_completed_manifest_missing_raw_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any
) -> None:
    monkeypatch.setattr(
        module, "unresolved_ids", lambda *_args: ({"before"}, 1, TARGET)
    )
    client = _Client()
    _invoke(module, tmp_path, client)
    store = LocalArtifactStore(tmp_path / "artifacts")
    path = store.run_root("gmail.recovery_evidence", RUN)
    for file in (path / "raw").glob("*before*"):
        file.unlink()
    with pytest.raises(module.RecoveryEvidenceError, match="inconsistent RAW"):
        _invoke(module, tmp_path, client)
