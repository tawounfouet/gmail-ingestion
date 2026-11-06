"""RC8 source recovery captures are fresh, bounded and never historical RAW."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pyingestkit.artifacts.filesystem import LocalArtifactStore

RUN = UUID("00000000-0000-0000-0000-000000009301")


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("capture_rc8_current_source_evidence")


class _Messages:
    def __init__(self, data: dict[str, dict[str, Any]]) -> None:
        self.data = data
        self.calls = 0

    def get_message(self, mid: str, *, format: str) -> dict[str, Any]:
        assert format == "full"
        self.calls += 1
        return self.data[mid]


class _History:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def list_history(self, *, start_history_id: str, **kwargs: Any) -> dict[str, Any]:
        assert start_history_id == "110"
        return {"historyId": "115", "history": self.rows}


class _Client:
    def __init__(
        self, rows: dict[str, dict[str, Any]], changes: list[dict[str, Any]] | None = None,
    ) -> None:
        self.messages = _Messages(rows)
        self.history = _History(changes or [])


def _candidate() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "BLOCKED_SOURCE_QUALIFICATION_REQUIRED",
        "candidate_mode": "CURRENT_RECONCILIATION",
        "candidate_id": "rc8_test",
        "account_id": "acct_0123456789abcdef01234567",
        "source_history_id": "110",
        "source_id_set_sha256": "a" * 64,
        "exception_ledger_sha256": "b" * 64,
        "private_recovery_required_ids": ["msg-one"],
    }


def _payload(mid: str = "msg-one", history: str = "109") -> dict[str, Any]:
    return {
        "id": mid, "threadId": "thread-1", "historyId": history,
        "labelIds": ["INBOX"], "payload": {"mimeType": "text/plain"},
    }


def test_capture_writes_isolated_raw_and_private_manifest(
    module: Any, tmp_path: Path,
) -> None:
    store = LocalArtifactStore(tmp_path)
    result = module.capture(
        _Client({"msg-one": _payload()}),
        store=store,
        candidate=_candidate(),
        run_id=RUN,
    )
    assert result["recovery_message_count"] == 1
    assert result["current_pointer_promoted"] is False
    assert result["stable_release_qualified"] is False
    evidence = store.run_root("gmail.rc8_source_evidence", RUN)
    manifest = json.loads((evidence / "source_manifest.json").read_text(encoding="utf-8"))
    assert manifest["candidate_id"] == "rc8_test"
    assert manifest["recovery_message_ids"] == ["msg-one"]
    assert (evidence / "source_manifest.json").stat().st_mode & 0o077 == 0
    assert not (tmp_path / "accounts").exists()
    assert "msg-one" not in json.dumps(result)
    with pytest.raises(module.SourceCaptureError, match="already"):
        module.capture(
            _Client({"msg-one": _payload()}),
            store=store,
            candidate=_candidate(),
            run_id=RUN,
        )


def test_capture_refuses_source_change_since_inventory(
    module: Any, tmp_path: Path,
) -> None:
    change = [{"id": "111", "labelsAdded": [
        {"message": {"id": "msg-one"}, "labelIds": ["IMPORTANT"]}
    ]}]
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(module.SourceCaptureError, match="changed"):
        module.capture(
            _Client({"msg-one": _payload()}, change),
            store=store, candidate=_candidate(), run_id=RUN,
        )
    assert not (store.run_root("gmail.rc8_source_evidence", RUN) / "source_manifest.json").exists()


def test_capture_refuses_post_source_history_payload(
    module: Any, tmp_path: Path,
) -> None:
    with pytest.raises(module.SourceCaptureError, match="identity/scope"):
        module.capture(
            _Client({"msg-one": _payload(history="130")}),
            store=LocalArtifactStore(tmp_path),
            candidate=_candidate(), run_id=RUN,
        )


def test_capture_refuses_spam_message_misclassified_as_current(
    module: Any, tmp_path: Path,
) -> None:
    item = _payload()
    item["labelIds"] = ["INBOX", "SPAM"]
    with pytest.raises(module.SourceCaptureError, match="identity/scope"):
        module.capture(
            _Client({"msg-one": item}),
            store=LocalArtifactStore(tmp_path),
            candidate=_candidate(), run_id=RUN,
        )


def test_candidate_loader_rejects_missing_recovery_identity(
    module: Any, tmp_path: Path,
) -> None:
    source = tmp_path / "plan.json"
    payload = _candidate()
    del payload["private_recovery_required_ids"]
    source.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(module.SourceCaptureError, match="incomplete"):
        module._load_candidate(source)
