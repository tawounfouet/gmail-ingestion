"""Thin adapter around the Gmail API client."""

from __future__ import annotations

import importlib
from collections.abc import Mapping
from typing import Any, cast

from .client_error import GmailApiError
from .labels import GmailLabelsApi


class GmailApiClient:
    """Expose Gmail source methods without leaking Google SDK details upstream."""

    def __init__(self, *, credentials: Any | None = None, service: Any | None = None) -> None:
        if service is not None:
            self._service = service
        else:
            if credentials is None:
                raise ValueError("credentials or service is required")
            discovery = importlib.import_module("googleapiclient.discovery")
            build = cast(Any, discovery.build)
            self._service = build(
                "gmail",
                "v1",
                credentials=credentials,
                cache_discovery=False,
            )
        self.labels = GmailLabelsApi(self._service)

    def get_profile(self) -> Mapping[str, Any]:
        """Call users.getProfile for the authenticated mailbox."""
        try:
            response = self._service.users().getProfile(userId="me").execute()
        except Exception as exc:  # noqa: BLE001 - map Google SDK failures to a safe error
            raise GmailApiError("Unable to retrieve the authenticated Gmail profile") from exc
        if not isinstance(response, dict):
            raise GmailApiError("Gmail profile response is not a JSON object")
        return cast(Mapping[str, Any], response)
