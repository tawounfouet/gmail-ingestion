"""Gmail History API adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, cast

from .client_error import GmailApiError


class GmailHistoryExpiredError(GmailApiError):
    """Raised when Gmail rejects an invalid or expired startHistoryId."""


_ALLOWED_HISTORY_TYPES = {
    "labelAdded",
    "labelRemoved",
    "messageAdded",
    "messageDeleted",
}


class GmailHistoryApi:
    """Read-only adapter for users.history.list."""

    def __init__(self, service: Any) -> None:
        self._service = service

    def list_history(
        self,
        *,
        start_history_id: str,
        page_token: str | None = None,
        max_results: int = 500,
        history_types: Sequence[str] = (),
        label_id: str | None = None,
    ) -> Mapping[str, Any]:
        """Return one Gmail History page after the supplied history cursor."""
        if not start_history_id:
            raise ValueError("start_history_id must not be empty")
        if not 1 <= max_results <= 500:
            raise ValueError("max_results must be between 1 and 500")
        invalid_types = set(history_types) - _ALLOWED_HISTORY_TYPES
        if invalid_types:
            raise ValueError(
                "Unsupported Gmail history type(s): "
                + ", ".join(sorted(invalid_types))
            )

        request: dict[str, Any] = {
            "userId": "me",
            "startHistoryId": start_history_id,
            "maxResults": max_results,
        }
        if page_token:
            request["pageToken"] = page_token
        if history_types:
            request["historyTypes"] = list(history_types)
        if label_id:
            request["labelId"] = label_id

        try:
            response = self._service.users().history().list(**request).execute()
        except Exception as exc:  # noqa: BLE001 - sanitize Google SDK failures
            if _http_status(exc) == 404:
                raise GmailHistoryExpiredError(
                    "Gmail history checkpoint is invalid or expired; a full sync is required"
                ) from exc
            raise GmailApiError("Unable to list Gmail history") from exc

        if not isinstance(response, dict):
            raise GmailApiError("Gmail history.list response is not a JSON object")
        history = response.get("history", [])
        if history is not None and not isinstance(history, list):
            raise GmailApiError("Gmail history.list response contains an invalid history field")
        history_id = response.get("historyId")
        if history_id is not None and not isinstance(history_id, str):
            raise GmailApiError("Gmail history.list response contains an invalid historyId")
        return cast(Mapping[str, Any], response)


def _http_status(exc: BaseException) -> int | None:
    response = getattr(exc, "resp", None)
    status = getattr(response, "status", None)
    if isinstance(status, int):
        return status
    status_code = getattr(exc, "status_code", None)
    return status_code if isinstance(status_code, int) else None
