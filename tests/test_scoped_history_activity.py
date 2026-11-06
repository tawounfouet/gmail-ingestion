"""History-only diagnostic and privacy-safe scoped inventory failure tests."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("diagnose_scoped_history_activity")


class FakeHistory:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.calls = 0

    def list_history(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["start_history_id"] == "100"
        result = self.pages[self.calls]
        self.calls += 1
        return result


def test_history_activity_summarizes_without_message_ids(module: Any) -> None:
    source = FakeHistory([
        {"historyId": "104", "nextPageToken": "more", "history": [
            {"id": "101", "messagesAdded": [
                {"message": {"id": "private-id"}}]},
            {"id": "103", "labelsRemoved": [
                {"message": {"id": "private-id"}, "labelIds": ["SPAM"]}]},
        ]},
        {"historyId": "108", "history": [
            {"id": "107", "labelsAdded": [
                {"message": {"id": "private-id"}, "labelIds": ["STARRED"]}],
             "messagesDeleted": [{"message": {"id": "private-id"}}]},
        ]},
    ])
    report = module.diagnose_history(source, start_history_id="100", max_pages=3)
    assert report["history_pages"] == 2
    assert report["history_records"] == 3
    assert report["start_history_id"] == "100"
    assert report["end_history_id"] == "108"
    assert report["message_add_entries"] == 1
    assert report["message_delete_entries"] == 1
    assert report["scope_membership_label_changes"] == 1
    assert report["would_block_excluded_scope_if_during_scan"] is True
    assert report["release_qualified"] is False
    assert "private-id" not in json.dumps(report)


def test_history_activity_no_change(module: Any) -> None:
    report = module.diagnose_history(
        FakeHistory([{"historyId": "100"}]), start_history_id="100"
    )
    assert report["history_records"] == 0
    assert report["would_block_excluded_scope_if_during_scan"] is False


def test_history_activity_page_cap_fails_closed(module: Any) -> None:
    with pytest.raises(module.HistoryActivityIncompleteError if hasattr(
        module, "HistoryActivityIncompleteError"
    ) else module.HistoryActivityIncomplete, match="page cap"):
        module.diagnose_history(
            FakeHistory([{"historyId": "101", "nextPageToken": "p2"}]),
            start_history_id="100",
            max_pages=1,
        )


def test_history_activity_rejects_repeated_token(module: Any) -> None:
    with pytest.raises(module.HistoryActivityIncomplete, match="repeated"):
        module.diagnose_history(
            FakeHistory([
                {"historyId": "100", "nextPageToken": "p2"},
                {"historyId": "100", "nextPageToken": "p2"},
            ]),
            start_history_id="100",
        )


def test_history_activity_rejects_malformed_label_ids(module: Any) -> None:
    with pytest.raises(module.HistoryActivityIncomplete, match="label IDs"):
        module.diagnose_history(
            FakeHistory([{"historyId": "102", "history": [
                {"id": "101", "labelsAdded": [{"labelIds": "SPAM"}]}
            ]}]),
            start_history_id="100",
        )
