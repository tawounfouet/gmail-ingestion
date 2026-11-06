"""RC8 source cohort 404 preflight: privacy, source drift and no RAW writes."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest

from gmail_ingestion.api import GmailMessageNotFoundError


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("diagnose_rc8_capture_cohort")


def _candidate() -> dict[str, Any]:
    return {
        "candidate_id": "rc8_test",
        "account_id": "acct_test",
        "source_history_id": "15906422",
        "private_recovery_required_ids": ["id-alive", "id-gone"],
    }


def _payload(mid: str, *, history: str = "15906420", labels: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": mid,
        "threadId": "private-thread",
        "historyId": history,
        "labelIds": ["INBOX"] if labels is None else labels,
        "payload": {"mimeType": "text/plain", "body": {"data": "secret"}},
    }


class _Messages:
    def __init__(self, messages: dict[str, Any]) -> None:
        self.messages = messages
        self.calls = 0

    def get_message(self, mid: str, *, format: str) -> dict[str, Any]:
        assert format == "full"
        self.calls += 1
        if mid not in self.messages:
            raise GmailMessageNotFoundError("Not found")
        return self.messages[mid]


class _History:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.calls = 0

    def list_history(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["start_history_id"] == "15906422"
        assert kwargs["max_results"] == 500
        next_page = self.pages[self.calls]
        self.calls += 1
        return next_page


class _Client:
    def __init__(self, messages: dict[str, Any], pages: list[dict[str, Any]]) -> None:
        self.messages = _Messages(messages)
        self.history = _History(pages)


def _deleted(mid: str) -> dict[str, Any]:
    return {"message": {"id": mid, "threadId": "private-thread"}}


def test_404_with_committed_delete_is_classified_but_not_silently_ignored(module: Any) -> None:
    client = _Client(
        {"id-alive": _payload("id-alive")},
        [{"historyId": "15906430", "history": [
            {"id": "15906426", "messagesDeleted": [_deleted("id-gone")]},
        ]}],
    )
    result = module.diagnose_cohort(client, candidate=_candidate())
    assert result["status"] == "BLOCKED_SOURCE_COHORT_REVALIDATION_REQUIRED"
    assert result["message_not_found_count"] == 1
    assert result["not_found_with_post_frontier_delete_count"] == 1
    assert result["not_found_without_post_frontier_delete_count"] == 0
    assert result["required_message_ids_with_explicit_delete_count"] == 1
    assert result["distinct_required_ids_blocked_count"] == 1
    assert result["persistent_writes"] == 0
    assert "id-alive" not in json.dumps(result)
    assert "id-gone" not in json.dumps(result)
    assert "private-thread" not in json.dumps(result)


def test_404_without_history_delete_is_unproven(module: Any) -> None:
    client = _Client(
        {"id-alive": _payload("id-alive")},
        [{"historyId": "15906430", "history": []}],
    )
    result = module.diagnose_cohort(client, candidate=_candidate())
    assert result["message_not_found_count"] == 1
    assert result["not_found_without_post_frontier_delete_count"] == 1
    assert result["required_message_ids_changed_since_inventory_count"] == 0
    assert result["stable_release_qualified"] is False


def test_full_messages_are_checked_for_scope_and_freshness(module: Any) -> None:
    client = _Client(
        {
            "id-alive": _payload("id-alive", history="15906450"),
            "id-gone": _payload("id-gone", labels=["TRASH"]),
        },
        [{"historyId": "15906455", "history": []}],
    )
    result = module.diagnose_cohort(client, candidate=_candidate())
    assert result["retrievable_full_count"] == 2
    assert result["payload_newer_than_source_frontier_count"] == 1
    assert result["currently_outside_source_scope_count"] == 1
    assert result["distinct_required_ids_blocked_count"] == 2


def test_valid_cohort_is_preflight_only_not_stable_qualification(module: Any) -> None:
    client = _Client(
        {
            "id-alive": _payload("id-alive"),
            "id-gone": _payload("id-gone"),
        },
        [{"historyId": "15906430", "history": []}],
    )
    result = module.diagnose_cohort(client, candidate=_candidate())
    assert result["status"] == "CAPTURE_PREFLIGHT_PASS_NOT_RELEASE_QUALIFIED"
    assert result["retrievable_full_count"] == 2
    assert result["stable_release_qualified"] is False
    assert result["current_pointer_promoted"] is False
    assert result["gmail_message_content_persisted"] is False


def test_changed_message_history_is_fail_closed(module: Any) -> None:
    client = _Client(
        {
            "id-alive": _payload("id-alive"),
            "id-gone": _payload("id-gone"),
        },
        [{"historyId": "15906430", "history": [
            {"id": "15906426", "labelsAdded": [
                {"message": {"id": "id-alive"}, "labelIds": ["STARRED"]}
            ]}
        ]}],
    )
    result = module.diagnose_cohort(client, candidate=_candidate())
    assert result["required_message_ids_changed_since_inventory_count"] == 1
    assert result["status"] == "BLOCKED_SOURCE_COHORT_REVALIDATION_REQUIRED"


def test_history_page_cap_does_not_claim_success(module: Any) -> None:
    client = _Client(
        {"id-alive": _payload("id-alive"), "id-gone": _payload("id-gone")},
        [{"historyId": "15906425", "history": [], "nextPageToken": "next"}],
    )
    with pytest.raises(module.SourceCaptureError, match="cap reached"):
        module.diagnose_cohort(client, candidate=_candidate(), max_history_pages=1)


def test_wrong_source_scope_and_duplicate_recovery_ids_rejected(module: Any) -> None:
    candidate = _candidate()
    candidate["private_recovery_required_ids"] = ["same", "same"]
    with pytest.raises(module.SourceCaptureError, match="Invalid or oversized"):
        module.diagnose_cohort(_Client({}, []), candidate=candidate)


def test_malformed_history_event_is_rejected_without_ids(module: Any) -> None:
    client = _Client(
        {"id-alive": _payload("id-alive"), "id-gone": _payload("id-gone")},
        [{"historyId": "15906430", "history": [
            {"id": "15906426", "messagesDeleted": [{"message": {"id": ""}}]},
        ]}],
    )
    with pytest.raises(module.SourceCaptureError):
        module.diagnose_cohort(client, candidate=_candidate())
