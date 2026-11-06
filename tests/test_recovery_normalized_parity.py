"""Read-only semantic comparison tests for dated Gmail recovery RAW evidence."""

from __future__ import annotations

import base64
import importlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.persistence.raw_message_writer import RawMessageWriter

ACCOUNT = "acct_0123456789abcdef01234567"
EMAIL = "fixture@example.com"
BASE = UUID("00000000-0000-0000-0000-000000007001")
DELTA = UUID("00000000-0000-0000-0000-000000007002")
FIRST = UUID("00000000-0000-0000-0000-000000007003")
SECOND = UUID("00000000-0000-0000-0000-000000007004")
TARGET = "15694719"


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("qualify_recovery_normalization")


def _message(message_id: str, *, data: str, history_id: str) -> dict[str, Any]:
    return {
        "id": message_id,
        "threadId": "thread",
        "historyId": history_id,
        "snippet": "same",
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Content-Type", "value": "text/plain; charset=utf-8"},
                {"name": "Subject", "value": "not-for-output"},
            ],
            "body": {"size": 5, "data": data},
        },
    }


def _seed(
    root: Path,
    *,
    semantic_change: bool,
    corrupt: bool = False,
) -> None:
    store = LocalArtifactStore(root)
    writer = RawMessageWriter(store, job_id="gmail.recovery_evidence")
    hello = base64.urlsafe_b64encode(b"Hello").decode("ascii").rstrip("=")
    world = base64.urlsafe_b64encode(b"World").decode("ascii").rstrip("=")
    for run_id in (FIRST, SECOND):
        for message_id in ("stable", "equivalent", "changed"):
            data = hello
            if run_id == SECOND and message_id == "equivalent":
                data = hello + "="
            if run_id == SECOND and message_id == "changed" and semantic_change:
                data = world
            writer.write(
                run_id,
                message_id,
                _message(
                    message_id,
                    data=data,
                    history_id="15694718" if message_id != "changed" else "15694720",
                ),
            )
        meta = {
            "schema_version": 1,
            "status": "COMPLETE",
            "namespace": "gmail.recovery_evidence",
            "non_authoritative": True,
            "account_id": ACCOUNT,
            "baseline_run_id": str(BASE),
            "delta_run_ids": [str(DELTA)],
            "recovery_run_id": str(run_id),
            "historical_target_history_id": TARGET,
            "unique_ids_evaluated": 4,
            "captured_ids": ["stable", "equivalent", "changed"],
            "not_found_ids": ["gone"],
            "captured_current_full": 3,
            "not_found_404_now": 1,
        }
        if corrupt and run_id == SECOND:
            meta["account_id"] = "wrong"
        store.write_json(
            "gmail.recovery_evidence", run_id, "recovery_manifest.json", meta
        )


def _qualify(module: Any, root: Path) -> dict[str, object]:
    return module.qualify(
        artifact_root=root,
        account_id=ACCOUNT,
        email_address=EMAIL,
        baseline_run_id=BASE,
        delta_run_ids=(DELTA,),
        first_run_id=FIRST,
        second_run_id=SECOND,
        max_messages=4,
    )


def test_ignores_raw_base64_padding_drift_when_normalized_rows_match(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any,
) -> None:
    root = tmp_path / "artifacts"
    _seed(root, semantic_change=False)
    monkeypatch.setattr(
        module, "unresolved_ids",
        lambda *_args: ({"stable", "equivalent", "changed", "gone"}, 4, TARGET),
    )
    before = sorted((str(x.relative_to(root)), x.stat().st_size) for x in root.rglob("*"))
    result = _qualify(module, root)
    after = sorted((str(x.relative_to(root)), x.stat().st_size) for x in root.rglob("*"))
    assert result["identical_full_raw"] == 2
    assert result["different_full_raw"] == 1
    assert result["normalized_equivalent"] == 3
    assert result["normalized_different"] == 0
    assert result["source_history_bucket_counts"] == {"gt_target": 1, "le_target": 2}
    assert result["still_unavailable_in_both"] == 1
    assert result["historical_projection_qualified"] is False
    assert result["gmail_api_calls"] == 0
    assert result["persistent_writes"] == 0
    assert "not-for-output" not in json.dumps(result)
    assert "\"message_id\": \"equivalent\"" not in json.dumps(result)
    assert before == after


def test_detects_semantically_changed_body_without_printing_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any,
) -> None:
    root = tmp_path / "artifacts"
    _seed(root, semantic_change=True)
    monkeypatch.setattr(
        module, "unresolved_ids",
        lambda *_args: ({"stable", "equivalent", "changed", "gone"}, 4, TARGET),
    )
    result = _qualify(module, root)
    assert result["different_full_raw"] == 2
    assert result["normalized_different"] == 1
    assert result["changed_normalized_table_counts"] == {"messages": 1}
    assert result["normalized_changed_history_bucket_counts"] == {"gt_target": 1}
    assert "Hello" not in json.dumps(result)
    assert "World" not in json.dumps(result)


def test_mismatched_manifest_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any,
) -> None:
    root = tmp_path / "artifacts"
    _seed(root, semantic_change=False, corrupt=True)
    monkeypatch.setattr(
        module, "unresolved_ids",
        lambda *_args: ({"stable", "equivalent", "changed", "gone"}, 4, TARGET),
    )
    with pytest.raises(ValueError, match="identity/frontier"):
        _qualify(module, root)


def test_excessive_comparison_refused_before_loading_raw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any,
) -> None:
    monkeypatch.setattr(
        module, "unresolved_ids",
        lambda *_args: ({"stable", "equivalent", "changed", "gone"}, 4, TARGET),
    )
    with pytest.raises(ValueError, match="safety cap"):
        module.qualify(
            artifact_root=tmp_path,
            account_id=ACCOUNT,
            email_address=EMAIL,
            baseline_run_id=BASE,
            delta_run_ids=(DELTA,),
            first_run_id=FIRST,
            second_run_id=SECOND,
            max_messages=3,
        )
