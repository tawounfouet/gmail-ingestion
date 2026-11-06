"""RC8 source list/get audit must not mistake missing messages for proven deletions."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from gmail_ingestion.api import GmailMessageNotFoundError


@pytest.fixture
def audit(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("audit_rc8_list_get_consistency")


def _plan(ids: list[str]) -> dict[str, Any]:
    return {
        "candidate_id": "rc8_test",
        "source_history_id": "100",
        "private_recovery_required_ids": ids,
    }


def _payload(mid: str, *, history: str = "100", labels: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": mid,
        "threadId": "t-" + mid,
        "historyId": history,
        "labelIds": ["INBOX"] if labels is None else labels,
        "payload": {"mimeType": "text/plain", "body": {"data": "private-body"}},
    }


class _Messages:
    def __init__(self, bodies: dict[str, dict[str, Any]]) -> None:
        self.bodies = bodies
        self.calls = 0

    def get_message(self, mid: str, *, format: str) -> dict[str, Any]:
        assert format == "full"
        self.calls += 1
        if mid not in self.bodies:
            raise GmailMessageNotFoundError("not found")
        return self.bodies[mid]


class _History:
    def __init__(self, changes: list[dict[str, Any]] | None = None) -> None:
        self.changes = [] if changes is None else changes
        self.calls = 0

    def list_history(self, *, start_history_id: str, **kwargs: Any) -> dict[str, Any]:
        assert start_history_id == "120"
        self.calls += 1
        return {"historyId": "130", "history": self.changes}


class _Client:
    def __init__(
        self, bodies: dict[str, dict[str, Any]],
        changes: list[dict[str, Any]] | None = None,
    ) -> None:
        self.messages = _Messages(bodies)
        self.history = _History(changes)


def test_two_unlisted_404s_cause_new_plan_not_deletion(audit: Any) -> None:
    client = _Client({"a": _payload("a"), "b": _payload("b")})
    result = audit.audit_membership(
        client,
        candidate=_plan(["a", "b", "missing1", "missing2"]),
        current_ids={"a", "b", "unrelated"},
        current_history_id="120",
    )
    assert result["status"] == "REFRESH_FROZEN_RC8_PLAN_REQUIRED"
    assert result["not_listed_and_get_404_count"] == 2
    assert result["listed_but_get_404_count"] == 0
    assert result["distinct_cohort_ids_with_blockers_count"] == 2
    assert result["historical_raw_overwritten"] is False
    assert result["stable_release_qualified"] is False
    assert "missing1" not in json.dumps(result)
    assert "private-body" not in json.dumps(result)


def test_listed_message_get_404_blocks_source_qualification(audit: Any) -> None:
    client = _Client({"a": _payload("a")})
    result = audit.audit_membership(
        client,
        candidate=_plan(["a", "gone"]),
        current_ids={"a", "gone"},
        current_history_id="120",
    )
    assert result["status"] == "BLOCKED_SOURCE_LIST_GET_INCONSISTENCY"
    assert result["listed_but_get_404_count"] == 1
    assert result["not_listed_and_get_404_count"] == 0
    assert result["current_pointer_promoted"] is False


def test_unlisted_but_retrievable_is_contradiction(audit: Any) -> None:
    client = _Client({"a": _payload("a")})
    result = audit.audit_membership(
        client, candidate=_plan(["a"]), current_ids={"other"},
        current_history_id="120",
    )
    assert result["not_listed_but_full_retrievable_count"] == 1
    assert result["status"] == "BLOCKED_SOURCE_LIST_GET_INCONSISTENCY"


def test_history_mutation_blocks_even_when_list_get_agree(audit: Any) -> None:
    row = {
        "id": "125",
        "labelsAdded": [{
            "message": {"id": "a", "threadId": "t-a"}, "labelIds": ["STARRED"]
        }],
    }
    result = audit.audit_membership(
        _Client({"a": _payload("a")}, changes=[row]),
        candidate=_plan(["a"]),
        current_ids={"a"}, current_history_id="120",
    )
    assert result["changed_since_new_source_count"] == 1
    assert result["status"] == "BLOCKED_SOURCE_CHANGED_AFTER_REOBSERVATION"


def test_new_payload_and_scope_move_block_source(audit: Any) -> None:
    result = audit.audit_membership(
        _Client({"a": _payload("a", history="124", labels=["SPAM"])}),
        candidate=_plan(["a"]), current_ids={"a"}, current_history_id="120",
    )
    assert result["payload_newer_than_new_source_count"] == 1
    assert result["current_spam_trash_scope_count"] == 1
    assert result["status"] == "BLOCKED_SOURCE_CHANGED_AFTER_REOBSERVATION"


def test_exact_cohort_match_is_preflight_only(audit: Any) -> None:
    result = audit.audit_membership(
        _Client({"a": _payload("a")}),
        candidate=_plan(["a"]), current_ids={"a"}, current_history_id="120",
    )
    assert result["status"] == "SOURCE_COHORT_CONSISTENT_NOT_RELEASE_QUALIFIED"
    assert result["listed_and_full_retrievable_count"] == 1
    assert result["message_bodies_persisted"] == 0
    assert result["gmail_writes"] == 0
    assert result["stable_release_qualified"] is False


@pytest.mark.parametrize(
    "history, required",
    [("99", ["a"]), ("120", ["a", "a"]), ("120", ["a", "b"])],
)
def test_invalid_frontier_duplicate_or_oversized_cohort_fail_closed(
    audit: Any, history: str, required: list[str],
) -> None:
    with pytest.raises(audit.SourceCaptureError):
        audit.audit_membership(
            _Client({}), candidate=_plan(required), current_ids=set(),
            current_history_id=history, max_messages=1,
        )
