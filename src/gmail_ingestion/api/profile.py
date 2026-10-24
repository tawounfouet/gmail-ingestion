"""Mapping from Gmail profile payloads to the domain model."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from gmail_ingestion.domain import GmailAccount


class GmailProfileError(ValueError):
    """Raised when Gmail returns an unusable profile payload."""


def map_gmail_profile(account_id: str, payload: Mapping[str, Any]) -> GmailAccount:
    """Normalize users.getProfile into a typed GmailAccount."""
    email = payload.get("emailAddress")
    if not isinstance(email, str) or not email.strip():
        raise GmailProfileError("Gmail profile is missing emailAddress")

    return GmailAccount(
        account_id=account_id,
        email_address=email.strip(),
        messages_total=_optional_int(payload.get("messagesTotal"), "messagesTotal"),
        threads_total=_optional_int(payload.get("threadsTotal"), "threadsTotal"),
        history_id=_optional_str(payload.get("historyId")),
    )


def _optional_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise GmailProfileError(f"{field_name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise GmailProfileError(f"{field_name} must be an integer") from exc


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)
