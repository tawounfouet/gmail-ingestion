"""RC8 derived scoped inventory: source frontier alignment and race controls."""

from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("derive_scoped_inventory_from_all")


def _prior(path: Path, ids: list[str], frontier: str = "100") -> None:
    digest = hashlib.sha256(("\n".join(sorted(ids)) + "\n").encode()).hexdigest()
    path.write_text(json.dumps({
        "schema_version": 1,
        "status": "OBSERVED_NOT_RELEASE_QUALIFIED",
        "scope": "include_spam_trash=true; unfiltered Gmail messages",
        "history_start": frontier,
        "history_end": frontier,
        "history_catchup_complete": True,
        "current_projection_compared": False,
        "ids": ids,
        "message_count": len(ids),
        "id_set_sha256": digest,
    }), encoding="utf-8")


def _added(msg: str) -> dict[str, Any]:
    return {"message": {"id": msg, "threadId": "thread"}}


class _Messages:
    def __init__(self, pages: dict[str, list[dict[str, Any]]]) -> None:
        self.pages = pages
        self.calls: dict[str, int] = {"SPAM": 0, "TRASH": 0}

    def list_messages(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["include_spam_trash"] is True
        label = kwargs["label_ids"][0]
        i = self.calls[label]
        self.calls[label] += 1
        return self.pages[label][i]


class _History:
    def __init__(self, pages: dict[str, list[dict[str, Any]]]) -> None:
        self.pages = pages
        self.calls: dict[str, int] = {}

    def list_history(self, **kwargs: Any) -> dict[str, Any]:
        start = kwargs["start_history_id"]
        self.calls[start] = self.calls.get(start, 0) + 1
        return self.pages[start][self.calls[start] - 1]


class _Client:
    def __init__(
        self, pages: dict[str, list[dict[str, Any]]],
        history: dict[str, list[dict[str, Any]]],
        starts: tuple[str, str] = ("110", "110"),
    ) -> None:
        self.messages = _Messages(pages)
        self.history = _History(history)
        self.starts = iter(starts)

    def get_profile(self) -> dict[str, str]:
        return {"historyId": next(self.starts)}


def _default_client(
    *, changes_since_prior: list[dict[str, Any]] | None = None,
    window: list[dict[str, Any]] | None = None,
    ends: tuple[str, str] = ("110", "110"),
) -> _Client:
    return _Client(
        {"SPAM": [{"messages": [{"id": "spam"}]}],
         "TRASH": [{"messages": [{"id": "trash"}]}]},
        {"110": [{"historyId": ends[1], "history": window or []}],
         "100": [{"historyId": ends[1], "history": changes_since_prior or []}]},
        starts=ends,
    )


def test_derives_matching_scope_without_rescanning_all_mail(
    module: Any, tmp_path: Path,
) -> None:
    inventory = tmp_path / "all.json"
    _prior(inventory, ["old", "trash", "spam", "erased"])
    source = _default_client(
        changes_since_prior=[
            {"id": "101", "messagesAdded": [_added("arrived")]},
            {"id": "102", "messagesDeleted": [_added("erased")]},
        ],
    )
    report = module.derive_inventory(source, previous_inventory=inventory)
    assert report["ids"] == ["arrived", "old"]
    assert report["message_count"] == 2
    assert report["history_end"] == "110"
    assert report["spam_pages"] == 1
    assert report["trash_pages"] == 1
    assert report["history_add_entries_replayed"] == 1
    assert report["history_delete_entries_replayed"] == 1
    assert report["stable_release_qualified"] is False
    assert "old" not in json.dumps({k: v for k, v in report.items() if k != "ids"})
    assert source.messages.calls == {"SPAM": 1, "TRASH": 1}


def test_scope_changes_during_label_list_fail_closed(
    module: Any, tmp_path: Path,
) -> None:
    inventory = tmp_path / "all.json"
    _prior(inventory, ["old", "trash", "spam"])
    source = _default_client(
        window=[{"id": "111", "labelsAdded": [
            {"message": {"id": "old"}, "labelIds": ["SPAM"]}
        ]}],
        ends=("110", "111"),
    )
    with pytest.raises(module.SourceReconciliationError, match="scope changes"):
        module.derive_inventory(source, previous_inventory=inventory)


def test_message_add_during_label_list_fails_closed(
    module: Any, tmp_path: Path,
) -> None:
    inventory = tmp_path / "all.json"
    _prior(inventory, ["old", "trash", "spam"])
    source = _default_client(
        window=[{"id": "111", "messagesAdded": [_added("new")]}],
        ends=("110", "111"),
    )
    with pytest.raises(module.SourceReconciliationError, match="scope changes"):
        module.derive_inventory(source, previous_inventory=inventory)


def test_normal_label_changes_during_label_list_are_safe(
    module: Any, tmp_path: Path,
) -> None:
    inventory = tmp_path / "all.json"
    _prior(inventory, ["old", "trash", "spam"])
    changes = [{"id": "111", "labelsAdded": [
        {"message": {"id": "old"}, "labelIds": ["IMPORTANT"]}
    ]}]
    source = _default_client(
        window=changes,
        changes_since_prior=changes,
        ends=("110", "111"),
    )
    report = module.derive_inventory(source, previous_inventory=inventory)
    assert report["ids"] == ["old"]
    assert report["quiet_window_history_records"] == 1


def test_label_reference_not_in_full_inventory_fails_closed(
    module: Any, tmp_path: Path,
) -> None:
    inventory = tmp_path / "all.json"
    _prior(inventory, ["old"])
    source = _default_client()
    with pytest.raises(module.SourceReconciliationError, match="absent"):
        module.derive_inventory(source, previous_inventory=inventory)


def test_inventory_and_history_pagination_caps(module: Any) -> None:
    fake = _Messages({"SPAM": [{"messages": [], "nextPageToken": "p2"}]})
    class Client:
        messages = fake

    with pytest.raises(module.SourceReconciliationError, match="page cap"):
        module._read_label_ids(Client(), "SPAM", max_pages=1, page_size=500)
