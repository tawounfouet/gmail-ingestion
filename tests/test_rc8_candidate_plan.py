"""RC8 isolated candidate: explicit provenance, scope identity, and no promotion."""

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
from gmail_ingestion.projection import ProjectionDeltaBatch

ACCOUNT = "acct_0123456789abcdef01234567"
BASE = UUID("00000000-0000-0000-0000-000000009201")
DELTA = UUID("00000000-0000-0000-0000-000000009202")


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("plan_rc8_current_candidate")


def _event(
    kind: GmailHistoryEventType, mid: str, history: str, ordinal: int = 0
) -> GmailHistoryEvent:
    return GmailHistoryEvent(
        event_id=f"evt-{history}-{ordinal}",
        account_id=ACCOUNT,
        history_id=history,
        event_type=kind,
        message_id=mid,
        thread_id="t",
        label_id=None,
        event_ordinal=ordinal,
        source_page_token=None,
        observed_at=datetime(2026, 11, 6, tzinfo=UTC),
        run_id=DELTA,
    ).mark_processed()


def _scoped_source(path: Path, ids: list[str], *, scope: str | None = None) -> None:
    sha = hashlib.sha256(("\n".join(sorted(ids)) + "\n").encode()).hexdigest()
    path.write_text(json.dumps({
        "schema_version": 1,
        "status": "OBSERVED_NOT_RELEASE_QUALIFIED",
        "scope": scope or "include_spam_trash=false; unfiltered Gmail messages",
        "include_spam_trash": False,
        "source_scope_evidence_version": 1,
        "history_catchup_complete": True,
        "history_end": "110",
        "ids": ids,
        "message_count": len(ids),
        "id_set_sha256": sha,
        "source_all_inventory_sha256": "a" * 64,
    }), encoding="utf-8")


def _setup(
    module: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, tuple[UUID, ...]]:
    inventory = tmp_path / "private.json"
    _scoped_source(inventory, ["base", "missing-current", "raw-current"])
    base = tmp_path / "runs" / "gmail" / "messages" / str(BASE)
    base.mkdir(parents=True)
    (base / "manifest.json").write_text(json.dumps({
        "run_id": str(BASE),
        "account_id": ACCOUNT,
        "status": "COMPLETED",
        "mode": "full_snapshot",
        "include_spam_trash": False,
        "message_limit": None,
        "filters": {
            "query": None, "label_ids": [],
            "after": None, "before": None, "last_days": None,
        },
    }), encoding="utf-8")
    monkeypatch.setattr(module, "read_baseline_ids", lambda *_: ({"base"}, "100", 1))
    events = (
        _event(GmailHistoryEventType.MESSAGE_ADDED, "missing-current", "101"),
        _event(GmailHistoryEventType.MESSAGE_ADDED, "missing-gone", "102"),
        _event(GmailHistoryEventType.MESSAGE_ADDED, "raw-current", "103"),
        _event(GmailHistoryEventType.MESSAGE_DELETED, "missing-gone", "104"),
    )
    batch = ProjectionDeltaBatch(
        account_id=ACCOUNT, run_id=DELTA,
        input_history_id="100", output_history_id="110",
        events=events,
    )
    class BatchReader:
        def __init__(self, _store: Any) -> None:
            pass

        def read_many(self, _ids: Any) -> tuple[ProjectionDeltaBatch, ...]:
            return (batch,)

    class RawWriter:
        def __init__(self, _store: Any) -> None:
            pass

        def exists(self, _rid: UUID, mid: str) -> bool:
            return mid == "raw-current"

    monkeypatch.setattr(module, "LocalProjectionBatchReader", BatchReader)
    monkeypatch.setattr(module, "RawMessageWriter", RawWriter)
    return inventory, (DELTA,)


def test_candidate_is_source_bound_unpromotable_and_private(
    module: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src, delta_ids = _setup(module, tmp_path, monkeypatch)
    result = module.build_candidate_plan(
        artifact_root=tmp_path,
        account_id=ACCOUNT,
        baseline_run_id=BASE,
        delta_run_ids=delta_ids,
        source_inventory=src,
        min_free_gib=1,
    )
    assert result["candidate_mode"] == "CURRENT_RECONCILIATION"
    assert result["status"] == "BLOCKED_SOURCE_QUALIFICATION_REQUIRED"
    assert result["current_pointer_promoted"] is False
    assert result["stable_release_qualified"] is False
    assert result["events_missing_original_raw"] == 2
    assert result["missing_classifications"] == {
        "SOURCE_PRESENT_ORIGINAL_RAW_MISSING": 1,
        "SUPERSEDED_BY_COMMITTED_DELETE": 1,
    }
    assert result["distinct_missing_message_ids"] == 2
    assert result["id_fold_source_only_count"] == 0
    assert result["id_fold_local_only_count"] == 0
    assert result["candidate_id"].startswith("rc8_")
    private = tmp_path / "candidate.json"
    module.write_private_inventory(private, result)
    assert private.stat().st_mode & 0o077 == 0
    public = {k: v for k, v in result.items() if k != "private_exception_ledger"}
    assert "missing-current" not in json.dumps(public)
    assert "missing-gone" not in json.dumps(public)


def test_source_scope_does_not_accept_all_mail_inventory(
    module: Any, tmp_path: Path,
) -> None:
    path = tmp_path / "private.json"
    _scoped_source(
        path, ["one"],
        scope="include_spam_trash=true; unfiltered Gmail messages",
    )
    with pytest.raises(module.RC8CandidateError, match="scoped source"):
        module._source(path)


def test_no_unproven_source_absence_becomes_a_delete(module: Any) -> None:
    events = [_event(GmailHistoryEventType.MESSAGE_ADDED, "lost-or-spam", "101")]
    class NoRaw:
        def exists(self, *_args: Any) -> bool:
            return False

    missing, classes = module._classify_missing(events, set(), NoRaw())
    assert classes == {"ABSENT_FROM_SCOPED_SOURCE_UNPROVEN": 1}
    assert missing[0]["status"] != "SUPERSEDED_BY_COMMITTED_DELETE"


def test_candidate_reproducible_identification(
    module: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    src, delta_ids = _setup(module, tmp_path, monkeypatch)
    opts = dict(
        artifact_root=tmp_path, account_id=ACCOUNT, baseline_run_id=BASE,
        delta_run_ids=delta_ids, source_inventory=src, min_free_gib=1,
    )
    first = module.build_candidate_plan(**opts)
    second = module.build_candidate_plan(**opts)
    assert first["candidate_id"] == second["candidate_id"]
    assert first["exception_ledger_sha256"] == second["exception_ledger_sha256"]


def test_no_delta_chain_is_rejected(module: Any, tmp_path: Path) -> None:
    with pytest.raises(module.RC8CandidateError, match="delta run"):
        module.build_candidate_plan(
            artifact_root=tmp_path, account_id=ACCOUNT, baseline_run_id=BASE,
            delta_run_ids=(), source_inventory=tmp_path / "missing.json",
        )
