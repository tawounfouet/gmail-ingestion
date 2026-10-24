"""Gmail Messages API adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from .client_error import GmailApiError


class GmailMessagesApi:
    """Read-only adapter for users.messages.list and users.messages.get."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def list_messages(
        self,
        *,
        page_token: str | None = None,
        max_results: int = 500,
        include_spam_trash: bool = False,
        query: str | None = None,
        label_ids: Sequence[str] = (),
    ) -> Mapping[str, Any]:
        """Return one discovery page of Gmail message references."""
        if not 1 <= max_results <= 500:
            raise ValueError("max_results must be between 1 and 500")

        request: dict[str, Any] = {
            "userId": "me",
            "maxResults": max_results,
            "includeSpamTrash": include_spam_trash,
        }
        if page_token:
            request["pageToken"] = page_token
        if query:
            request["q"] = query
        if label_ids:
            request["labelIds"] = list(label_ids)

        try:
            response = self._service.users().messages().list(**request).execute()
        except Exception as exc:  # noqa: BLE001 - sanitize Google SDK failures
            raise GmailApiError("Unable to list Gmail messages") from exc

        if not isinstance(response, dict):
            raise GmailApiError("Gmail messages.list response is not a JSON object")
        messages = response.get("messages", [])
        if not isinstance(messages, list):
            raise GmailApiError("Gmail messages.list response contains an invalid messages field")
        return cast(Mapping[str, Any], response)

    def get_message(self, message_id: str, *, format: str = "full") -> Mapping[str, Any]:
        """Hydrate one Gmail message resource."""
        if not message_id:
            raise ValueError("message_id must not be empty")
        if format not in {"minimal", "metadata", "full", "raw"}:
            raise ValueError("Unsupported Gmail message format")

        try:
            response = (
                self._service.users()
                .messages()
                .get(userId="me", id=message_id, format=format)
                .execute()
            )
        except Exception as exc:  # noqa: BLE001 - sanitize Google SDK failures
            raise GmailApiError(f"Unable to retrieve Gmail message {message_id!r}") from exc

        if not isinstance(response, dict):
            raise GmailApiError("Gmail messages.get response is not a JSON object")
        return cast(Mapping[str, Any], response)
