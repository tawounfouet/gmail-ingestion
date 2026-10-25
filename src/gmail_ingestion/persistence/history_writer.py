"""Persistence adapters for Gmail History pages and normalized events."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

from pyingestkit.artifacts.base import ArtifactStore
from pyingestkit.core.exceptions import StorageError

from gmail_ingestion.domain import GmailHistoryEvent

_HISTORY_JOB_ID = "gmail.history"


class RawHistoryWriter:
    """Persist and replay immutable users.history.list responses."""

    def __init__(self, store: ArtifactStore, *, job_id: str = _HISTORY_JOB_ID) -> None:
        self.store = store
        self.job_id = job_id

    def read_page(self, run_id: UUID, page_index: int) -> Mapping[str, Any] | None:
        """Return an already frozen History page for deterministic same-run replay."""
        if page_index < 0:
            raise ValueError("page_index must be >= 0")
        relative_path = f"raw/{self._name(page_index)}"
        try:
            raw = self.store.read_bytes(self.store.uri_for(self.job_id, run_id, relative_path))
        except StorageError:
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Persisted Gmail History page {page_index} is invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Persisted Gmail History page {page_index} must be an object")
        return cast(Mapping[str, Any], payload)

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
        name = self._name(page_index)
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

    @staticmethod
    def _name(page_index: int) -> str:
        return f"history-page-{page_index:06d}.json"


class HistoryEventWriter:
    """Persist normalized elementary history events under deterministic IDs."""

    def __init__(self, store: ArtifactStore, *, job_id: str = _HISTORY_JOB_ID) -> None:
        self.store = store
        self.job_id = job_id

    def read(self, run_id: UUID, event_id: str) -> GmailHistoryEvent | None:
        relative_path = f"history_events/{event_id}.json"
        try:
            raw = self.store.read_bytes(self.store.uri_for(self.job_id, run_id, relative_path))
        except StorageError:
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Persisted Gmail History event {event_id!r} is invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Persisted Gmail History event {event_id!r} must be an object")
        event = GmailHistoryEvent.from_payload(cast(Mapping[str, Any], payload))
        if event.event_id != event_id:
            raise ValueError(f"Persisted Gmail History event {event_id!r} has wrong event_id")
        return event

    def write(self, event: GmailHistoryEvent) -> str:
        path = self.store.write_json(
            self.job_id,
            event.run_id,
            f"history_events/{event.event_id}.json",
            event.to_payload(),
        )
        return str(path)


class IncrementalManifestWriter:
    """Persist and read run-level incremental transaction evidence."""

    def __init__(self, store: ArtifactStore, *, job_id: str = _HISTORY_JOB_ID) -> None:
        self.store = store
        self.job_id = job_id

    def read(self, run_id: UUID) -> Mapping[str, Any] | None:
        try:
            raw = self.store.read_bytes(
                self.store.uri_for(self.job_id, run_id, "incremental_manifest.json")
            )
        except StorageError:
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Persisted incremental manifest is invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("Persisted incremental manifest must be an object")
        return cast(Mapping[str, Any], payload)

    def write(self, run_id: UUID, payload: Mapping[str, Any]) -> str:
        path = self.store.write_json(
            self.job_id,
            run_id,
            "incremental_manifest.json",
            dict(payload),
        )
        return str(path)
