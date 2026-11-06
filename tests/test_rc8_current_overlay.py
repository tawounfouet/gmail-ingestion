"""Pure RC8 Current overlay: source membership, relations and no fake History."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from gmail_ingestion.application.rc8_current_reconciliation import (
    RC8MaterializationError,
    reconcile_current_rows,
)
from gmail_ingestion.projection.message_delta import MESSAGE_PROJECTION_TABLES

ACCOUNT = "acct_0123456789abcdef01234567"
NOW = datetime(2026, 11, 6, tzinfo=UTC)


def _bundle(mid: str, *, label: str = "INBOX", account: str = ACCOUNT) -> dict[str, list[dict[str, Any]]]:
    return {
        "messages": [{
            "account_id": account, "message_id": mid, "thread_id": f"t-{mid}",
            "internal_date": NOW, "snippet": "test",
            "has_attachments": False, "history_id": "100",
        }],
        "message_headers": [{"account_id": account, "message_id": mid, "part_id": "root"}],
        "message_recipients": [],
        "message_labels": [{
            "account_id": account, "message_id": mid,
            "label_id": label, "is_active": True,
        }],
        "mime_parts": [{"account_id": account, "message_id": mid, "part_path": "root"}],
        "attachments": [],
    }


def _baseline(*bundles: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    return {
        table: [row for bundle in bundles for row in bundle[table]]
        for table in MESSAGE_PROJECTION_TABLES
    }


def test_overlay_reconciles_source_only_and_out_of_scope_without_fake_events() -> None:
    baseline = _baseline(_bundle("base"), _bundle("became-spam"))
    recovery = {"moved-out-of-trash": _bundle("moved-out-of-trash")}
    rows, diagnostics = reconcile_current_rows(
        account_id=ACCOUNT,
        baseline_tables=baseline,
        prepared_deltas=(),
        source_message_ids={"base", "moved-out-of-trash"},
        current_recovery_rows=recovery,
        committed_events=(),
    )
    assert {row["message_id"] for row in rows["messages"]} == {
        "base", "moved-out-of-trash"
    }
    assert len(rows["threads"]) == 2
    assert diagnostics["outside_source_pruned_count"] == 1
    assert diagnostics["source_only_recovered_count"] == 1
    assert diagnostics["historical_event_ids_unchanged"] == 0
    assert len(baseline["messages"]) == 2


def test_source_only_without_fresh_raw_fails_closed() -> None:
    with pytest.raises(RC8MaterializationError, match="lack independently"):
        reconcile_current_rows(
            account_id=ACCOUNT,
            baseline_tables=_baseline(_bundle("base")),
            prepared_deltas=(),
            source_message_ids={"base", "new"},
            current_recovery_rows={},
            committed_events=(),
        )


def test_source_overlay_rejects_foreign_account() -> None:
    with pytest.raises(RC8MaterializationError, match="cross-message or cross-account"):
        reconcile_current_rows(
            account_id=ACCOUNT,
            baseline_tables=_baseline(_bundle("base")),
            prepared_deltas=(),
            source_message_ids={"base", "new"},
            current_recovery_rows={"new": _bundle("new", account="acct_other")},
            committed_events=(),
        )


def test_overlay_rejects_spam_label_on_included_message() -> None:
    with pytest.raises(RC8MaterializationError, match="Spam or Trash"):
        reconcile_current_rows(
            account_id=ACCOUNT,
            baseline_tables=_baseline(_bundle("base", label="SPAM")),
            prepared_deltas=(),
            source_message_ids={"base"},
            current_recovery_rows={},
            committed_events=(),
        )
