"""Gmail Attachments API adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from .client_error import GmailApiError


class GmailAttachmentsApi:
    """Read-only adapter for users.messages.attachments.get."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def get_attachment(self, message_id: str, attachment_id: str) -> Mapping[str, Any]:
        """Fetch one Gmail MessagePartBody containing attachment data."""
        if not message_id:
            raise ValueError("message_id must not be empty")
        if not attachment_id:
            raise ValueError("attachment_id must not be empty")
        try:
            response = (
                self._service.users()
                .messages()
                .attachments()
                .get(userId="me", messageId=message_id, id=attachment_id)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001 - sanitize Google SDK failures
            raise GmailApiError(
                f"Unable to retrieve Gmail attachment {attachment_id!r} "
                f"for message {message_id!r}"
            ) from exc
        if not isinstance(response, dict):
            raise GmailApiError("Gmail attachments.get response is not a JSON object")
        data = response.get("data")
        if data is not None and not isinstance(data, str):
            raise GmailApiError("Gmail attachments.get response contains invalid data")
        return cast(Mapping[str, Any], response)
