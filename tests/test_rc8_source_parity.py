"""RC8 logical Gmail parity is strict over message, labels, MIME and threads."""

from __future__ import annotations

from typing import Any

import pytest

from gmail_ingestion.quality.rc8_source_parity import (
    RC8SourceParityError,
    compare_message_bundle,
)


def _bundle() -> dict[str, list[dict[str, Any]]]:
    return {
        "messages": [{"message_id": "mid", "thread_id": "t", "snippet": "hello"}],
        "message_headers": [{"message_id": "mid", "header_name": "Subject", "header_value": "hello"}],
        "message_recipients": [{"message_id": "mid", "recipient_role": "TO"}],
        "message_labels": [{"message_id": "mid", "label_id": "INBOX", "is_active": True}],
        "mime_parts": [{"message_id": "mid", "part_path": "root", "mime_type": "text/plain"}],
        "attachments": [{"message_id": "mid", "part_path": "root", "filename": "test.txt"}],
    }


def test_source_current_bundle_parity_passes_and_ignores_physical_fields() -> None:
    source = _bundle()
    current = _bundle()
    current["messages"][0]["dataset_version"] = "rc8-candidate"
    current["message_labels"][0]["first_seen_at"] = "historical timestamp"
    current["attachments"][0]["download_status"] = "DOWNLOADED"
    result = compare_message_bundle(
        message_id="mid", source_rows=source,
        current_rows=current, thread_ids={"t"},
    )
    assert len(result) == 7
    assert all(result.values())


@pytest.mark.parametrize(
    ("table", "field", "value"),
    [
        ("messages", "snippet", "different"),
        ("message_labels", "label_id", "STARRED"),
        ("mime_parts", "mime_type", "text/html"),
        ("attachments", "filename", "wrong.txt"),
        ("message_recipients", "recipient_role", "BCC"),
        ("message_headers", "header_value", "tampered"),
    ],
)
def test_source_current_bundle_detects_concrete_difference(
    table: str, field: str, value: str,
) -> None:
    current = _bundle()
    current[table][0][field] = value
    with pytest.raises(RC8SourceParityError, match=f"Current mismatch in {table}"):
        compare_message_bundle(
            message_id="mid", source_rows=_bundle(),
            current_rows=current, thread_ids={"t"},
        )


def test_source_current_rejects_missing_thread() -> None:
    with pytest.raises(RC8SourceParityError, match="thread"):
        compare_message_bundle(
            message_id="mid", source_rows=_bundle(),
            current_rows=_bundle(), thread_ids={"other"},
        )


def test_cross_message_child_row_is_rejected() -> None:
    current = _bundle()
    current["attachments"][0]["message_id"] = "other"
    with pytest.raises(RC8SourceParityError, match="Cross-message"):
        compare_message_bundle(
            message_id="mid", source_rows=_bundle(),
            current_rows=current, thread_ids={"t"},
        )
