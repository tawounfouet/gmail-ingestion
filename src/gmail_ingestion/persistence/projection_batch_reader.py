"""Local adapter turning committed 0.4 incremental artifacts into projection delta batches."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.domain import GmailHistoryEvent, HistoryProcessingStatus
from gmail_ingestion.projection import ProjectionDeltaBatch

from .history_writer import IncrementalManifestWriter

_ALLOWED_STATUSES = frozenset({"COMPLETED", "RECOVERED_COMMIT"})


class ProjectionBatchReadError(RuntimeError):
    """Raised when persisted incremental evidence cannot form a trusted projection batch."""


class LocalProjectionBatchReader:
    """Read one committed Gmail History run as a projection delta batch."""

    def __init__(self, store: LocalArtifactStore) -> None:
        self.store = store
        self.manifests = IncrementalManifestWriter(store)

    def read(self, run_id: UUID) -> ProjectionDeltaBatch:
        """Load manifest + PROCESSED events and fail closed on incomplete evidence."""
        manifest = self.manifests.read(run_id)
        if manifest is None:
            raise ProjectionBatchReadError(
                f"incremental manifest does not exist for run {run_id}"
            )
        status = _required_string(manifest, "status")
        if status not in _ALLOWED_STATUSES:
            raise ProjectionBatchReadError(
                f"incremental run {run_id} is not committed: status={status!r}"
            )
        manifest_run_id = UUID(_required_string(manifest, "run_id"))
        if manifest_run_id != run_id:
            raise ProjectionBatchReadError("incremental manifest run_id is inconsistent")
        account_id = _required_string(manifest, "account_id")
        input_history_id = _required_string(manifest, "input_checkpoint")
        output_history_id = _required_string(manifest, "committed_checkpoint")

        event_dir = self.store.run_root("gmail.history", run_id) / "history_events"
        paths = sorted(event_dir.glob("*.json")) if event_dir.is_dir() else []
        events = tuple(self._read_event(path, run_id, account_id) for path in paths)

        expected_count = manifest.get("events_persisted")
        if (
            isinstance(expected_count, int)
            and not isinstance(expected_count, bool)
            and expected_count >= 0
            and expected_count != len(events)
        ):
            raise ProjectionBatchReadError(
                "incremental event evidence is incomplete: "
                f"manifest={expected_count}, files={len(events)}"
            )

        return ProjectionDeltaBatch(
            account_id=account_id,
            run_id=run_id,
            input_history_id=input_history_id,
            output_history_id=output_history_id,
            events=events,
        )

    def read_many(self, run_ids: tuple[UUID, ...]) -> tuple[ProjectionDeltaBatch, ...]:
        """Read an explicit ordered sequence of committed delta runs."""
        return tuple(self.read(run_id) for run_id in run_ids)

    @staticmethod
    def _read_event(path: Path, run_id: UUID, account_id: str) -> GmailHistoryEvent:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectionBatchReadError(f"invalid History event file {path}") from exc
        if not isinstance(payload, dict):
            raise ProjectionBatchReadError(f"History event file {path} must be an object")
        try:
            event = GmailHistoryEvent.from_payload(cast(Mapping[str, Any], payload))
        except (TypeError, ValueError) as exc:
            raise ProjectionBatchReadError(f"invalid History event payload {path}") from exc
        if event.run_id != run_id:
            raise ProjectionBatchReadError(f"History event {event.event_id} has wrong run_id")
        if event.account_id != account_id:
            raise ProjectionBatchReadError(
                f"History event {event.event_id} belongs to another account"
            )
        if event.processing_status is not HistoryProcessingStatus.PROCESSED:
            raise ProjectionBatchReadError(
                f"History event {event.event_id} is not PROCESSED"
            )
        return event


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ProjectionBatchReadError(f"{key} must be a non-empty string")
    return value
