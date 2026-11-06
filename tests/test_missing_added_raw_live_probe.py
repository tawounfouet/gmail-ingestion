"""Unit tests for minimal, content-free Gmail availability probing."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from gmail_ingestion.api import GmailApiError, GmailMessageNotFoundError


@pytest.fixture
def probe_module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    sys.modules.pop("probe_missing_added_raw_live", None)
    return importlib.import_module("probe_missing_added_raw_live")


class _FakeMinimalClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get_message(self, message_id: str, *, format: str = "minimal") -> Mapping[str, Any]:
        self.calls.append((message_id, format))
        if message_id == "gone":
            raise GmailMessageNotFoundError("Not found")
        if message_id == "error":
            raise GmailApiError("API unavailable")
        return {"id": message_id, "snippet": "ignored"}


def test_probe_only_minimal_get_and_aggregated_counts(probe_module: Any) -> None:
    client = _FakeMinimalClient()
    result = probe_module.probe(
        {"exists", "gone"},
        client,
        history_id="15694719",
        event_count=2,
        max_probes=3,
    )
    assert result["status"] == "COMPLETE"
    assert result["accessible_now"] == 1
    assert result["not_found_404_now"] == 1
    assert result["gmail_api_calls"] == 2
    assert result["historical_target_history_id"] == "15694719"
    assert client.calls == [("exists", "minimal"), ("gone", "minimal")]
    assert "exists" not in str(result)
    assert "gone" not in str(result)


def test_probe_fails_closed_on_non_404_error(probe_module: Any) -> None:
    client = _FakeMinimalClient()
    with pytest.raises(RuntimeError, match="Non-404"):
        probe_module.probe(
            {"error"}, client, history_id="200", event_count=1, max_probes=2
        )


def test_probe_refuses_to_exceed_safety_cap(probe_module: Any) -> None:
    client = _FakeMinimalClient()
    with pytest.raises(ValueError, match="safety limit"):
        probe_module.probe(
            {"a", "b"}, client, history_id="200", event_count=2, max_probes=1
        )
    assert client.calls == []
