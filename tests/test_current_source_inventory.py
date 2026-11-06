"""Bounded independent Gmail ID inventory and History convergence tests."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("observe_current_source_inventory")


class _Messages:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.calls = 0

    def list_messages(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["include_spam_trash"] is True
        item = self.pages[self.calls]
        self.calls += 1
        return item


class _History:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.calls = 0

    def list_history(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["start_history_id"] == "100"
        response = self.pages[self.calls]
        self.calls += 1
        return response


class _Client:
    def __init__(
        self, msg_pages: list[dict[str, Any]], hist_pages: list[dict[str, Any]]
    ) -> None:
        self.messages = _Messages(msg_pages)
        self.history = _History(hist_pages)

    def get_profile(self) -> dict[str, str]:
        return {"historyId": "100"}


def _event(message_id: str) -> dict[str, Any]:
    return {"message": {"id": message_id, "threadId": "thread"}}


def test_paginated_inventory_applies_post_listing_adds_and_deletes(
    module: Any, tmp_path: Path
) -> None:
    client = _Client(
        [
            {"messages": [{"id": "kept"}, {"id": "deleted"}], "nextPageToken": "page2"},
            {"messages": [{"id": "kept"}]},
        ],
        [
            {"historyId": "120", "history": [
                {"id": "101", "messagesAdded": [_event("new")]},
                {"id": "102", "messagesDeleted": [_event("deleted")]},
            ], "nextPageToken": "h2"},
            {"historyId": "125", "history": []},
        ],
    )
    result = module.observe_inventory(client, max_message_pages=3, max_history_pages=3)
    assert result["history_start"] == "100"
    assert result["history_end"] == "125"
    assert result["message_pages"] == 2
    assert result["history_pages"] == 2
    assert result["ids"] == ["kept", "new"]
    assert result["message_count"] == 2
    assert result["history_catchup_complete"] is True
    assert result["stable_release_qualified"] is False

    out = tmp_path / "private" / "gmail-ids.json"
    module.write_private_inventory(out, result)
    assert json.loads(out.read_text(encoding="utf-8")) == result
    assert out.stat().st_mode & 0o077 == 0
    with pytest.raises(FileExistsError):
        module.write_private_inventory(out, result)


def test_partial_message_pagination_never_returns_inventory(module: Any) -> None:
    client = _Client([{"nextPageToken": "more"}], [])
    with pytest.raises(module.InventoryIncompleteError, match="page cap"):
        module.observe_inventory(client, max_message_pages=1)
    assert client.history.calls == 0


def test_partial_history_pagination_is_not_accepted(module: Any) -> None:
    client = _Client(
        [{"messages": [{"id": "x"}]}],
        [{"historyId": "101", "history": [], "nextPageToken": "second"}],
    )
    with pytest.raises(module.InventoryIncompleteError, match="page cap"):
        module.observe_inventory(client, max_history_pages=1)


def test_repeated_page_tokens_rejected(module: Any) -> None:
    client = _Client(
        [
            {"nextPageToken": "repeat"},
            {"nextPageToken": "repeat"},
        ], []
    )
    with pytest.raises(module.InventoryIncompleteError, match="repeated"):
        module.observe_inventory(client, max_message_pages=3)


def test_invalid_history_does_not_synthesize_checkpoint(module: Any) -> None:
    client = _Client([{"messages": []}], [{"history": []}])
    with pytest.raises(module.InventoryIncompleteError, match="historyId"):
        module.observe_inventory(client)


def test_no_message_ids_in_public_report(module: Any) -> None:
    report = {"ids": ["secret-message-id"], "message_count": 1, "history_end": "101"}
    public = {key: value for key, value in report.items() if key != "ids"}
    assert "secret-message-id" not in json.dumps(public)
