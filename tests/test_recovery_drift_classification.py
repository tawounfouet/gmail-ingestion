"""Privacy-safe classification of changed Gmail RAW evidence fields."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.persistence.raw_message_writer import RawMessageWriter

ACCOUNT = "acct_0123456789abcdef01234567"
BASE = UUID("00000000-0000-0000-0000-000000000661")
DELTA = UUID("00000000-0000-0000-0000-000000000662")
FIRST = UUID("00000000-0000-0000-0000-000000000663")
SECOND = UUID("00000000-0000-0000-0000-000000000664")
TARGET = "15694719"


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("classify_recovery_drift")


def _seed(root: Path, *, changed: bool) -> None:
    store = LocalArtifactStore(root)
    writer = RawMessageWriter(store, job_id="gmail.recovery_evidence")
    for run_id in (FIRST, SECOND):
        for message_id in ("stable", "changing"):
            value = "old"
            if changed and run_id == SECOND and message_id == "changing":
                value = "new"
            writer.write(
                run_id,
                message_id,
                {
                    "id": message_id,
                    "threadId": "thread",
                    "historyId": TARGET,
                    "labelIds": ["INBOX"],
                    "payload": {
                        "mimeType": "text/plain",
                        "headers": [{"name": "Subject", "value": "not-for-output"}],
                        "body": {"size": 3, "data": "original"},
                    },
                    "snippet": value if message_id == "changing" else "unchanged",
                },
            )
        store.write_json(
            "gmail.recovery_evidence",
            run_id,
            "recovery_manifest.json",
            {
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
                "captured_ids": ["stable", "changing"],
                "not_found_ids": ["gone"],
                "captured_current_full": 2,
                "not_found_404_now": 1,
            },
        )


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ({"snippet": "a"}, {"snippet": "b"}, {"snippet"}),
        ({"historyId": "1"}, {"historyId": "2"}, {"history_id"}),
        ({"labelIds": ["INBOX"]}, {"labelIds": ["STARRED"]}, {"label_ids"}),
        (
            {"payload": {"headers": [{"name": "Subject", "value": "a"}]}},
            {"payload": {"headers": [{"name": "Subject", "value": "b"}]}},
            {"mime_headers"},
        ),
        (
            {"payload": {"parts": [{"body": {"data": "a"}}]}},
            {"payload": {"parts": [{"body": {"data": "b"}}]}},
            {"mime_body"},
        ),
        (
            {"payload": {"parts": []}},
            {"payload": {"parts": [{"mimeType": "text/plain"}]}},
            {"mime_structure"},
        ),
        ({"custom": "a"}, {"custom": "b"}, {"other_source_field"}),
    ],
)
def test_change_classification_uses_fixed_groups(
    module: Any, old: Any, new: Any, expected: set[str]
) -> None:
    assert module.changed_groups(old, new) == expected


def test_offline_comparison_prints_aggregates_and_preserves_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any
) -> None:
    root = tmp_path / "artifacts"
    _seed(root, changed=True)
    monkeypatch.setattr(
        module, "unresolved_ids",
        lambda *_args: ({"stable", "changing", "gone"}, 3, TARGET),
    )
    before = {
        str(p.relative_to(root)): p.stat().st_size for p in root.rglob("*")
    }
    result = module.classify(
        artifact_root=root,
        account_id=ACCOUNT,
        baseline_run_id=BASE,
        delta_run_ids=(DELTA,),
        first_run_id=FIRST,
        second_run_id=SECOND,
    )
    after = {
        str(p.relative_to(root)): p.stat().st_size for p in root.rglob("*")
    }
    assert result["status"] == "CLASSIFIED"
    assert result["compared_captured"] == 2
    assert result["changed_messages"] == 1
    assert result["exactly_identical_messages"] == 1
    assert result["changed_field_group_message_counts"] == {"snippet": 1}
    assert result["historical_projection_qualified"] is False
    assert result["gmail_api_calls"] == 0
    assert result["persistent_writes"] == 0
    assert "changing" not in json.dumps(result)
    assert "not-for-output" not in json.dumps(result)
    assert before == after


def test_mismatched_capture_inventory_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, module: Any
) -> None:
    root = tmp_path / "artifacts"
    _seed(root, changed=False)
    monkeypatch.setattr(
        module, "unresolved_ids",
        lambda *_args: ({"stable", "changing", "different"}, 3, TARGET),
    )
    with pytest.raises(ValueError, match="inventory"):
        module.classify(
            artifact_root=root,
            account_id=ACCOUNT,
            baseline_run_id=BASE,
            delta_run_ids=(DELTA,),
            first_run_id=FIRST,
            second_run_id=SECOND,
        )
