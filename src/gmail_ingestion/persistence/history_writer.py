"""Persistence adapters for Gmail History pages and normalized events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any
from uuid import UUID

from pyingestkit.artifacts.base import ArtifactStore

from gmail_ingestion.domain import GmailHistoryEvent

_HISTORY_JOB_ID = "gmail.history"


class RawHistoryWriter:
    """Persist immutable users.history.list responses for one incremental run."""

    def __init__(self, store: ArtifactStore, *, job_id: str = _HISTORY_JOB_ID) -> None:
        self.store = store
        self.job_id = job_id

    def write_page(
        self,
        run_id: UUID,
        page_index: int,
        payload: Mapping[str, Any],
        *,
        start_history_id: str,
        page_token: str | None,
    ) -> str:
        if page_index < 0:
            raise ValueError("page_index must be >= 0")
        name = f"history-page-{page_index:06d}.json"
        data = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
            + "\n"
        ).encode("utf-8")
        source_uri = (
            "gmail://users/me/history"
            f"?startHistoryId={start_history_id}"
            f"&pageToken={page_token or ''}"
        )
        artifact = self.store.write_raw(
            self.job_id,
            run_id,
            name=name,
            data=data,
            source_uri=source_uri,
            content_type="application/json",
        )
        return str(artifact.location_uri)


class HistoryEventWriter:
    """Persist normalized elementary history events under deterministic IDs."""

    def __init__(self, store: ArtifactStore, *, job_id: str = _HISTORY_JOB_ID) -> None:
        self.store = store
        self.job_id = job_id

    def write(self, event: GmailHistoryEvent) -> str:
        path = self.store.write_json(
            self.job_id,
            event.run_id,
            f"history_events/{event.event_id}.json",
            event.to_payload(),
        )
        return str(path)


class IncrementalManifestWriter:
    """Persist run-level evidence for the b1 incremental transaction."""

    def __init__(self, store: ArtifactStore, *, job_id: str = _HISTORY_JOB_ID) -> None:
        self.store = store
        self.job_id = job_id

    def write(self, run_id: UUID, payload: Mapping[str, Any]) -> str:
        path = self.store.write_json(
            self.job_id,
            run_id,
            "incremental_manifest.json",
            dict(payload),
        )
        return str(path)
