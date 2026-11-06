"""Compare two immutable recovery evidence runs without network access or writes."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.persistence.raw_message_writer import RawMessageWriter

ACCOUNT = "acct_0123456789abcdef01234567"
BASE = UUID("00000000-0000-0000-0000-000000000551")
DELTA = UUID("00000000-0000-0000-0000-000000000552")
FIRST = UUID("00000000-0000-0000-0000-000000000553")
SECOND = UUID("00000000-0000-0000-0000-000000000554")
TARGET = "15694719"


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    sys.modules.pop("compare_recovery_evidence", None)
    return importlib.import_module("compare_recovery_evidence")


def _seed(
    root: Path,
    *,
    changed: bool = False,
    incomplete_second: bool = False,
) -> None:
    store = LocalArtifactStore(root)
    writer = RawMessageWriter(store, job_id="gmail.recovery_evidence")
    for run_id in (FIRST, SECOND):
        captured = ["m1", "m2"]
        for message_id in captured:
            if incomplete_second and run_id == SECOND and message_id == "m2":
                continue
            payload: dict[str, Any] = {
                "id": message_id,
                "threadId": "t",
                "historyId": "15694718" if message_id == "m1" else "15694720",
                "labelIds": ["INBOX"],
            }
            if changed and run_id == SECOND and message_id == "m1":
                payload["labelIds"] = ["STARRED"]
                payload["historyId"] = "15694719"
            writer.write(run_id, message_id, payload)
        manifest = {
            "schema_version": 1,
            "status": "COMPLETE",
            "namespace": "gmail.recovery_evidence",
            "non_authoritative": True,
            "account_id": ACCOUNT,
            "baseline_run_id": str(BASE),
            "delta_run_ids": [str(DELTA)],
            "recovery_run_id": str(run_id),
            "historical_target_history_id": TARGET,
            "unique_ids_evaluated": 3,
            "captured_ids": captured,
            "not_found_ids": ["m3"],
            "captured_current_full": 2,
            "not_found_404_now": 1,
            "observed_at_utc": "2026-11-06T18:10:00+00:00",
        }
        store.write_json("gmail.recovery_evidence", run_id, "recovery_manifest.json", manifest)


def _run(module: Any, root: Path) -> dict[str, object]:
    return module.compare(
        artifact_root=root,
        account_id=ACCOUNT,
        baseline_run_id=BASE,
        delta_run_ids=(DELTA,),
        first_run_id=FIRST,
        second_run_id=SECOND,
    )


def test_identical_full_captures_and_no_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any
) -> None:
    root = tmp_path / "artifacts"
    _seed(root)
    monkeypatch.setattr(
        module, "unresolved_ids", lambda *_args: ({"m1", "m2", "m3"}, 3, TARGET)
    )
    before = sorted((str(p.relative_to(root)), p.stat().st_size) for p in root.rglob("*"))
    result = _run(module, root)
    after = sorted((str(p.relative_to(root)), p.stat().st_size) for p in root.rglob("*"))
    assert result["status"] == "COMPARED"
    assert result["matched_captured"] == 2
    assert result["identical_full_json"] == 2
    assert result["changed_full_json"] == 0
    assert result["matched_history_id_le_target"] == 1
    assert result["matched_history_id_gt_target"] == 1
    assert result["not_found_sets_match"] is True
    assert result["two_capture_payloads_stable"] is True
    assert result["historical_projection_qualified"] is False
    assert result["gmail_api_calls"] == 0
    assert result["persistent_writes"] == 0
    assert "m1" not in json.dumps(result)
    assert before == after


def test_changed_labels_detected_without_identifiers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any
) -> None:
    root = tmp_path / "artifacts"
    _seed(root, changed=True)
    monkeypatch.setattr(
        module, "unresolved_ids", lambda *_args: ({"m1", "m2", "m3"}, 3, TARGET)
    )
    result = _run(module, root)
    assert result["identical_full_json"] == 1
    assert result["changed_full_json"] == 1
    assert result["changed_label_sets"] == 1
    assert result["changed_message_history_ids"] == 1
    assert result["two_capture_payloads_stable"] is False


def test_completed_manifest_without_raw_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any
) -> None:
    root = tmp_path / "artifacts"
    _seed(root, incomplete_second=True)
    monkeypatch.setattr(
        module, "unresolved_ids", lambda *_args: ({"m1", "m2", "m3"}, 3, TARGET)
    )
    with pytest.raises(module.RecoveryComparisonError, match="missing or invalid"):
        _run(module, root)


def test_wrong_manifest_identity_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any
) -> None:
    root = tmp_path / "artifacts"
    _seed(root)
    monkeypatch.setattr(
        module, "unresolved_ids", lambda *_args: ({"m1", "m2", "m3"}, 3, TARGET)
    )
    path = LocalArtifactStore(root).run_root(
        "gmail.recovery_evidence", FIRST
    ) / "recovery_manifest.json"
    data = json.loads(path.read_text())
    data["account_id"] = "other"
    path.write_text(json.dumps(data))
    with pytest.raises(module.RecoveryComparisonError, match="identity/frontier"):
        _run(module, root)
