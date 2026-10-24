"""Gmail Labels API adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from .client_error import GmailApiError


class GmailLabelsApi:
    """Read-only adapter for users.labels.list and users.labels.get."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def list_labels(self) -> Mapping[str, Any]:
        """List all labels for the authenticated mailbox."""
        try:
            response = self._service.users().labels().list(userId="me").execute()
        except Exception as exc:  # noqa: BLE001 - sanitize Google SDK failures
            raise GmailApiError("Unable to list Gmail labels") from exc
        if not isinstance(response, dict):
            raise GmailApiError("Gmail labels.list response is not a JSON object")
        labels = response.get("labels", [])
        if not isinstance(labels, list):
            raise GmailApiError("Gmail labels.list response contains an invalid labels field")
        return cast(Mapping[str, Any], response)

    def get_label(self, label_id: str) -> Mapping[str, Any]:
        """Fetch one detailed label resource."""
        if not label_id:
            raise ValueError("label_id must not be empty")
        try:
            response = (
                self._service.users().labels().get(userId="me", id=label_id).execute()
            )
        except Exception as exc:  # noqa: BLE001 - sanitize Google SDK failures
            raise GmailApiError(f"Unable to retrieve Gmail label {label_id!r}") from exc
        if not isinstance(response, dict):
            raise GmailApiError("Gmail labels.get response is not a JSON object")
        return cast(Mapping[str, Any], response)
