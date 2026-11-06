"""Offline release preflight must never turn missing RAW into an implicit GO."""

from __future__ import annotations

import importlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from gmail_ingestion.domain import GmailHistoryEvent, GmailHistoryEventType

ACCOUNT = "acct_0123456789abcdef01234567"
RUN = UUID("00000000-0000-0000-0000-000000009001")
BASE = UUID("00000000-0000-0000-0000-000000009002")


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("audit_current_release_readiness")


def _event(
    msg: str,
    change: GmailHistoryEventType,
    history_id: str,
    ordinal: int = 0,
) -> GmailHistoryEvent:
    return GmailHistoryEvent(
        event_id=f"event-{history_id}-{ordinal}",
        account_id=ACCOUNT,
        history_id=history_id,
        event_type=change,
        message_id=msg,
        thread_id="thread",
        label_id=None,
        event_ordinal=ordinal,
        source_page_token=None,
        observed_at=datetime(2026, 11, 6, tzinfo=UTC),
        run_id=RUN,
    ).mark_processed()


def test_missing_raw_requires_a_later_committed_delete(module: Any) -> None:
    events = [
        _event("erased", GmailHistoryEventType.MESSAGE_ADDED, "100"),
        _event("private-missing-id", GmailHistoryEventType.MESSAGE_ADDED, "101"),
        _event("erased", GmailHistoryEventType.MESSAGE_DELETED, "102"),
        _event("with-raw", GmailHistoryEventType.MESSAGE_ADDED, "103"),
    ]
    summary = module.classify_missing_additions(
        events, lambda _run_id, message_id: message_id == "with-raw"
    )
    assert summary == {
        "superseded_by_committed_delete": 1,
        "unresolved_without_event_local_raw": 1,
        "total_missing_addition_events": 2,
    }
    printable = json.dumps(summary)
    assert "erased" not in printable
    assert "private-missing-id" not in printable
    assert "with-raw" not in printable


def test_deletion_before_last_add_does_not_resolve_it(module: Any) -> None:
    events = [
        _event("readded", GmailHistoryEventType.MESSAGE_ADDED, "100"),
        _event("readded", GmailHistoryEventType.MESSAGE_DELETED, "101"),
        _event("readded", GmailHistoryEventType.MESSAGE_ADDED, "102"),
    ]
    summary = module.classify_missing_additions(events, lambda *_: False)
    assert summary["superseded_by_committed_delete"] == 1
    assert summary["unresolved_without_event_local_raw"] == 1


def test_earlier_capture_cannot_satisfy_event_local_raw(module: Any) -> None:
    # The policy deliberately knows only whether the original event run has RAW.
    added = [_event("captured-later", GmailHistoryEventType.MESSAGE_ADDED, "100")]
    summary = module.classify_missing_additions(added, lambda *_: False)
    assert summary["unresolved_without_event_local_raw"] == 1


def test_input_validation_fails_before_artifact_access(
    module: Any, tmp_path: Path
) -> None:
    opts = dict(
        artifact_root=tmp_path,
        account_id=ACCOUNT,
        baseline_run_id=BASE,
        historical_delta_run_ids=(RUN,),
        catchup_run_id=RUN,
        first_recovery_run_id=UUID("00000000-0000-0000-0000-000000009003"),
        second_recovery_run_id=UUID("00000000-0000-0000-0000-000000009004"),
        expected_checkpoint="15903183",
    )
    with pytest.raises(ValueError, match="distinct"):
        module.preflight(**opts)
    opts["catchup_run_id"] = UUID("00000000-0000-0000-0000-000000009005")
    opts["expected_checkpoint"] = "not-a-checkpoint"
    with pytest.raises(ValueError, match="checkpoint"):
        module.preflight(**opts)


def test_non_numeric_history_fails_closed(module: Any) -> None:
    events = [_event("x", GmailHistoryEventType.MESSAGE_ADDED, "not-history")]
    with pytest.raises(ValueError):
        module.classify_missing_additions(events, lambda *_: False)
