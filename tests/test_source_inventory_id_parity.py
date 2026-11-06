"""Privacy and fail-closed tests for the independent message-ID parity audit."""

from __future__ import annotations

import hashlib
import importlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from gmail_ingestion.domain import GmailHistoryEvent, GmailHistoryEventType

ACCOUNT = "acct_0123456789abcdef01234567"
BASE = UUID("00000000-0000-0000-0000-000000009101")
DELTA = UUID("00000000-0000-0000-0000-000000009102")


@pytest.fixture
def audit_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("compare_source_inventory_ids")


def _source_inventory(path: Path, ids: list[str], *, end: str = "200") -> None:
    digest = hashlib.sha256(("\n".join(sorted(ids)) + "\n").encode("utf-8")).hexdigest()
    path.write_text(
        json.dumps({
            "schema_version": 1,
            "status": "OBSERVED_NOT_RELEASE_QUALIFIED",
            "history_catchup_complete": True,
            "scope": "include_spam_trash=true; unfiltered Gmail messages",
            "current_projection_compared": False,
            "history_start": end,
            "history_end": end,
            "ids": ids,
            "message_count": len(ids),
            "id_set_sha256": digest,
        }),
        encoding="utf-8",
    )


def _event(message_id: str, kind: GmailHistoryEventType, history: str) -> GmailHistoryEvent:
    return GmailHistoryEvent(
        event_id="evt-" + history,
        account_id=ACCOUNT,
        history_id=history,
        event_type=kind,
        message_id=message_id,
        thread_id="thread",
        label_id=None,
        event_ordinal=0,
        source_page_token=None,
        observed_at=datetime(2026, 11, 6, tzinfo=UTC),
        run_id=DELTA,
    ).mark_processed()


class _Store:
    def __init__(self, root: Path) -> None:
        self.root = root

    def run_root(self, name: str, run: UUID) -> Path:
        path = self.root / name / str(run)
        path.mkdir(parents=True, exist_ok=True)
        return path


def _manifest(store: _Store, *, include_spam_trash: bool) -> None:
    path = store.run_root("gmail.messages", BASE) / "manifest.json"
    path.write_text(
        json.dumps({
            "run_id": str(BASE),
            "account_id": ACCOUNT,
            "status": "COMPLETED",
            "mode": "full_snapshot",
            "message_limit": None,
            "filters": {"query": None, "label_ids": []},
            "include_spam_trash": include_spam_trash,
        }),
        encoding="utf-8",
    )


def test_valid_source_inventory_reads_ids_without_printing(
    audit_module: Any, tmp_path: Path
) -> None:
    path = tmp_path / "inventory.json"
    _source_inventory(path, ["a", "b"])
    ids, doc = audit_module.read_source_ids(path)
    assert ids == {"a", "b"}
    assert doc["history_end"] == "200"


def test_source_inventory_tampering_and_duplicate_ids_fail(
    audit_module: Any, tmp_path: Path
) -> None:
    path = tmp_path / "inventory.json"
    _source_inventory(path, ["a", "b"])
    data = json.loads(path.read_text(encoding="utf-8"))
    data["id_set_sha256"] = "bad"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(audit_module.InventoryParityError, match="digest"):
        audit_module.read_source_ids(path)
    _source_inventory(path, ["a", "a"])
    with pytest.raises(audit_module.InventoryParityError, match="duplicate"):
        audit_module.read_source_ids(path)


def test_baseline_scope_mismatch_does_not_run_projection(
    audit_module: Any, tmp_path: Path
) -> None:
    store = _Store(tmp_path)
    _manifest(store, include_spam_trash=False)
    with pytest.raises(audit_module.InventoryScopeMismatch, match="Spam/Trash"):
        audit_module.verify_baseline_scope(store, BASE, ACCOUNT)


def test_scope_compatible_baseline_passes(audit_module: Any, tmp_path: Path) -> None:
    store = _Store(tmp_path)
    _manifest(store, include_spam_trash=True)
    evidence = audit_module.verify_baseline_scope(store, BASE, ACCOUNT)
    assert evidence["include_spam_trash"] is True


def test_reducer_applies_typed_events_in_history_order(
    audit_module: Any,
) -> None:
    events = [
        _event("existing", GmailHistoryEventType.MESSAGE_DELETED, "103"),
        _event("new", GmailHistoryEventType.MESSAGE_ADDED, "102"),
        _event("future", GmailHistoryEventType.MESSAGE_ADDED, "105"),
    ]
    actual, counts = audit_module.reduce_ids({"existing"}, events, "104")
    assert actual == {"new"}
    assert counts == {"message_add_events_applied": 1, "message_delete_events_applied": 1}


def test_reconciliation_rejects_source_frontier_ahead_of_committed_history(
    audit_module: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _Store(tmp_path)
    _manifest(store, include_spam_trash=True)
    inv = tmp_path / "private.json"
    _source_inventory(inv, ["a"], end="200")

    class _Reader:
        def __init__(self, _store: Any) -> None:
            pass

        def read_many(self, _runs: Any) -> list[Any]:
            class Batch:
                output_history_id = "199"

            return [Batch()]

    monkeypatch.setattr(audit_module, "LocalArtifactStore", lambda _root: store)
    monkeypatch.setattr(audit_module, "LocalProjectionBatchReader", _Reader)
    with pytest.raises(audit_module.HistoryCoverageMissing):
        audit_module.audit(
            artifact_root=tmp_path,
            account_id=ACCOUNT,
            baseline_run_id=BASE,
            delta_run_ids=(DELTA,),
            source_inventory=inv,
        )
