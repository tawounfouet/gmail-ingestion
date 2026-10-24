"""Normalize Gmail Label resources into the stable labels dataset."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from pyingestkit.dataset import Dataset

from gmail_ingestion.domain import GmailLabel

LABEL_FIELDS = (
    "account_id",
    "label_id",
    "name",
    "label_type",
    "message_list_visibility",
    "label_list_visibility",
    "messages_total",
    "messages_unread",
    "threads_total",
    "threads_unread",
    "color_text",
    "color_background",
    "is_system",
    "ingested_at",
)


class GmailLabelNormalizationError(ValueError):
    """Raised when a Gmail label cannot satisfy the normalized contract."""


def normalize_labels(
    account_id: str,
    payloads: Sequence[Mapping[str, Any]],
    *,
    ingested_at: datetime | None = None,
) -> Dataset:
    """Build the PyIngestKit Dataset defined by the Gmail labels specification."""
    timestamp = ingested_at or datetime.now(UTC)
    rows = [_normalize_label(account_id, payload, timestamp).to_row() for payload in payloads]
    return Dataset(rows, fields=LABEL_FIELDS)


def _normalize_label(
    account_id: str,
    payload: Mapping[str, Any],
    ingested_at: datetime,
) -> GmailLabel:
    label_id = _required_str(payload.get("id"), "id")
    name = _required_str(payload.get("name"), "name")
    label_type = _optional_str(payload.get("type"))
    color = payload.get("color")
    color_mapping = color if isinstance(color, Mapping) else {}

    return GmailLabel(
        account_id=account_id,
        label_id=label_id,
        name=name,
        label_type=label_type,
        message_list_visibility=_optional_str(payload.get("messageListVisibility")),
        label_list_visibility=_optional_str(payload.get("labelListVisibility")),
        messages_total=_optional_int(payload.get("messagesTotal"), "messagesTotal"),
        messages_unread=_optional_int(payload.get("messagesUnread"), "messagesUnread"),
        threads_total=_optional_int(payload.get("threadsTotal"), "threadsTotal"),
        threads_unread=_optional_int(payload.get("threadsUnread"), "threadsUnread"),
        color_text=_optional_str(color_mapping.get("textColor")),
        color_background=_optional_str(color_mapping.get("backgroundColor")),
        is_system=label_type == "system",
        ingested_at=ingested_at,
    )


def _required_str(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GmailLabelNormalizationError(f"Gmail label is missing {field_name}")
    return value.strip()


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise GmailLabelNormalizationError(f"{field_name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise GmailLabelNormalizationError(f"{field_name} must be an integer") from exc
