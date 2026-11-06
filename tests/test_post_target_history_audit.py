"""Qualify bounded post-frontier History observation without mutating Gmail state."""

from __future__ import annotations

import importlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("audit_post_target_history")


class _History:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, str | None, int]] = []

    def list_history(
        self,
        *,
        start_history_id: str,
        page_token: str | None = None,
        max_results: int = 500,
        history_types: tuple[str, ...] = (),
        label_id: str | None = None,
    ) -> Mapping[str, Any]:
        self.calls.append((start_history_id, page_token, max_results))
        index = len(self.calls) - 1
        return self.pages[index]


def _change(message_id: str) -> dict[str, Any]:
    return {"message": {"id": message_id, "threadId": "hidden"}}


def test_two_page_history_classifies_later_deletions_and_labels_without_identifiers(
    module: Any,
) -> None:
    client = _History([
        {
            "historyId": "220",
            "history": [
                {
                    "id": "201",
                    "messagesDeleted": [_change("gone-one"), _change("unrelated")],
                    "labelsAdded": [{"message": {"id": "present"}, "labelIds": ["STARRED"]}],
                },
                {
                    "id": "202",
                    "messagesAdded": [_change("gone-two")],
                },
            ],
            "nextPageToken": "second",
        },
        {
            "historyId": "240",
            "history": [
                {
                    "id": "230",
                    "messagesDeleted": [_change("gone-two")],
                    "labelsRemoved": [
                        {"message": {"id": "present"}, "labelIds": ["INBOX"]}
                    ],
                },
            ],
        },
    ])
    result = module.sweep_history(
        client,
        start_history_id="200",
        unresolved_ids={"gone-one", "gone-two", "gone-three", "present", "later"},
        not_found_ids={"gone-one", "gone-two", "gone-three"},
        latest_capture_history_ids={"later"},
        max_pages=3,
        page_size=100,
    )
    assert client.calls == [("200", None, 100), ("200", "second", 100)]
    assert result["status"] == "COMPLETE"
    assert result["pages_scanned"] == 2
    assert result["history_records_scanned"] == 3
    assert result["404_with_post_target_deletion"] == 2
    assert result["404_with_post_target_addition"] == 1
    assert result["404_with_post_target_deletion_without_new_addition"] == 1
    assert result["404_without_post_target_deletion_evidence"] == 1
    assert result["captured_full_with_post_target_label_mutation"] == 1
    assert result["captured_full_history_id_gt_target_and_changed"] == 0
    assert result["post_target_change_entries_matching_cohort"] == 5
    assert result["historical_projection_qualified"] is False
    assert result["raw_artifact_writes"] == 0
    assert result["history_checkpoint_writes"] == 0
    printed = json.dumps(result)
    for forbidden in ["gone-one", "gone-two", "gone-three", "present", "later", "STARRED"]:
        assert f'"{forbidden}"' not in printed


def test_no_deletion_is_reported_only_after_all_pages(
    module: Any,
) -> None:
    pages = [
        {"historyId": "210", "history": [], "nextPageToken": "keep-going"},
        {"historyId": "220", "history": [{"id": "212", "messagesDeleted": [_change("gone")]}]},
    ]
    with pytest.raises(module.HistorySweepIncompleteError, match="safety cap"):
        module.sweep_history(
            _History(pages),
            start_history_id="200",
            unresolved_ids={"gone"},
            not_found_ids={"gone"},
            latest_capture_history_ids=set(),
            max_pages=1,
        )
    result = module.sweep_history(
        _History(pages),
        start_history_id="200",
        unresolved_ids={"gone"},
        not_found_ids={"gone"},
        latest_capture_history_ids=set(),
        max_pages=2,
    )
    assert result["404_with_post_target_deletion"] == 1
    assert result["404_without_post_target_deletion_evidence"] == 0


@pytest.mark.parametrize(
    "invalid",
    [
        {"historyId": "201", "history": [{"id": "200", "messagesDeleted": [_change("gone")]}]},
        {"historyId": "201", "history": [{"id": "201", "messagesDeleted": [{}]}]},
        {"historyId": "201", "history": [{"id": "201", "messagesDeleted": "wrong"}]},
        {"historyId": "201", "history": "wrong"},
        {"historyId": "invalid", "history": []},
    ],
)
def test_invalid_source_history_fails_closed(
    module: Any, invalid: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        module.sweep_history(
            _History([invalid]),
            start_history_id="200",
            unresolved_ids={"gone"},
            not_found_ids={"gone"},
            latest_capture_history_ids=set(),
        )


def test_repeated_page_tokens_fail_closed(module: Any) -> None:
    client = _History([
        {"historyId": "201", "history": [], "nextPageToken": "repeat"},
        {"historyId": "202", "history": [], "nextPageToken": "repeat"},
    ])
    with pytest.raises(ValueError, match="repeated"):
        module.sweep_history(
            client,
            start_history_id="200",
            unresolved_ids={"gone"},
            not_found_ids={"gone"},
            latest_capture_history_ids=set(),
        )


def test_invalid_cohorts_rejected_without_api_call(module: Any) -> None:
    client = _History([])
    with pytest.raises(ValueError, match="bounds"):
        module.sweep_history(
            client,
            start_history_id="200",
            unresolved_ids={"one"},
            not_found_ids={"other"},
            latest_capture_history_ids=set(),
        )
    assert client.calls == []
