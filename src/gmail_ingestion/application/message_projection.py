"""Application service for deterministic Gmail current-state materialization."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pyingestkit.artifacts.base import ArtifactStore
from pyingestkit.core.exceptions import StorageError

from gmail_ingestion._version import __version__
from gmail_ingestion.domain import (
    GmailAccount,
    GmailHistoryEvent,
    GmailHistoryEventType,
    HistoryProcessingStatus,
)
from gmail_ingestion.normalization import normalize_snapshot
from gmail_ingestion.persistence.projection_dataset_store import (
    LocalProjectionDatasetStore,
    NormalizedBaselineReader,
    ProjectionDatasetError,
)
from gmail_ingestion.persistence.projection_store import LocalProjectionStore
from gmail_ingestion.persistence.raw_message_writer import RawMessageWriter
from gmail_ingestion.projection.digest import logical_projection_digest
from gmail_ingestion.projection.message_delta import (
    CURRENT_PROJECTION_TABLES,
    MESSAGE_PROJECTION_TABLES,
    MessageProjectionResult,
    PreparedMessageDelta,
)
from gmail_ingestion.projection.message_reducer import MessageProjectionReducer
from gmail_ingestion.projection.models import ProjectionPlan, ProjectionStatus
from gmail_ingestion.projection.service import ProjectionFoundationService
from gmail_ingestion.projection.thread_reducer import recompute_threads


class MessageProjectionMaterializationError(RuntimeError):
    """Raised when persisted message deltas cannot produce a safe current-state projection."""


class MessageDeltaProjectionService:
    """Materialize current Gmail state from normalized baseline plus persisted deltas."""

    def __init__(
        self,
        artifact_store: ArtifactStore,
        projection_store: LocalProjectionStore,
        *,
        baseline_reader: NormalizedBaselineReader | None = None,
        dataset_store: LocalProjectionDatasetStore | None = None,
        reducer: MessageProjectionReducer | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.projection_store = projection_store
        self.foundation = ProjectionFoundationService(projection_store)
        self.baseline_reader = baseline_reader or NormalizedBaselineReader(artifact_store)
        self.dataset_store = dataset_store or LocalProjectionDatasetStore(projection_store)
        self.reducer = reducer or MessageProjectionReducer()
        self.raw_messages = RawMessageWriter(artifact_store)

    def materialize(
        self,
        plan: ProjectionPlan,
        events: Sequence[GmailHistoryEvent],
    ) -> MessageProjectionResult:
        """Build and validate a message projection without contacting Gmail."""
        candidate = self.projection_store.load_candidate(plan.account_id, plan.projection_id)
        if candidate is None:
            candidate = self.foundation.create_candidate(plan, created_at=plan.created_at)
        elif candidate.plan != plan:
            raise MessageProjectionMaterializationError(
                "Persisted projection candidate does not match requested plan"
            )

        if candidate.state.status is ProjectionStatus.NEW:
            candidate = self.foundation.begin_build(
                plan.account_id,
                plan.projection_id,
                started_at=plan.created_at,
            )
        elif candidate.state.status is ProjectionStatus.READY:
            rows = self.dataset_store.read_candidate(
                plan.account_id,
                plan.projection_id,
                table_names=CURRENT_PROJECTION_TABLES,
            )
            digest = logical_projection_digest(
                rows,
                table_names=CURRENT_PROJECTION_TABLES,
            )
            if candidate.state.logical_digest != digest:
                raise MessageProjectionMaterializationError(
                    "READY projection candidate data does not match persisted logical digest"
                )
            return _result(
                plan,
                events,
                rows,
                digest,
                duplicate_events_ignored=_duplicate_count(events),
            )
        elif candidate.state.status is not ProjectionStatus.BUILDING:
            raise MessageProjectionMaterializationError(
                f"Projection candidate is not materializable from {candidate.state.status.value}"
            )

        try:
            baseline = self.baseline_reader.read(plan.baseline_run_id, plan.account_id)
            prepared = [
                self._prepare_delta(plan, baseline.account, event)
                for event in events
            ]
            rows, duplicates = self.reducer.reduce(baseline.tables, prepared)
            rows["threads"] = recompute_threads(
                plan.account_id,
                rows["messages"],
                rows["message_recipients"],
                events,
            )
            digest = logical_projection_digest(
                rows,
                table_names=CURRENT_PROJECTION_TABLES,
            )
            write_result = self.dataset_store.write_candidate(
                plan.account_id,
                plan.projection_id,
                rows,
                logical_digest=digest,
                table_names=CURRENT_PROJECTION_TABLES,
            )
        except (ProjectionDatasetError, MessageProjectionMaterializationError, ValueError) as exc:
            self._best_effort_fail(plan, str(exc))
            raise MessageProjectionMaterializationError(str(exc)) from exc

        self.foundation.validate_candidate(
            plan.account_id,
            plan.projection_id,
            logical_digest=digest,
        )
        return _result(
            plan,
            events,
            rows,
            digest,
            duplicate_events_ignored=duplicates,
            row_counts=dict(write_result.row_counts),
        )

    def _prepare_delta(
        self,
        plan: ProjectionPlan,
        account: GmailAccount,
        event: GmailHistoryEvent,
    ) -> PreparedMessageDelta:
        _validate_event(plan, event)
        if event.event_type in {
            GmailHistoryEventType.MESSAGE_DELETED,
            GmailHistoryEventType.LABEL_ADDED,
            GmailHistoryEventType.LABEL_REMOVED,
        }:
            return PreparedMessageDelta(event=event)

        if event.event_type is not GmailHistoryEventType.MESSAGE_ADDED:
            raise MessageProjectionMaterializationError(
                f"Unsupported projection event {event.event_type.value}"
            )

        try:
            payload = self.raw_messages.read(event.run_id, event.message_id)
        except (StorageError, ValueError) as exc:
            raise MessageProjectionMaterializationError(
                f"Persisted RAW message is required for MESSAGE_ADDED {event.message_id!r}"
            ) from exc

        normalized = normalize_snapshot(
            account,
            (),
            (payload,),
            ingested_at=event.observed_at,
            dataset_version=plan.projection_id,
            source_connector_version=__version__,
        )
        rows = {
            name: normalized.rows(name)
            for name in MESSAGE_PROJECTION_TABLES
        }
        return PreparedMessageDelta(event=event, normalized_rows=rows)

    def _best_effort_fail(self, plan: ProjectionPlan, reason: str) -> None:
        candidate = self.projection_store.load_candidate(plan.account_id, plan.projection_id)
        if candidate is None:
            return
        if candidate.state.status not in {ProjectionStatus.NEW, ProjectionStatus.BUILDING}:
            return
        try:
            self.foundation.fail_candidate(
                plan.account_id,
                plan.projection_id,
                reason,
            )
        except Exception:  # noqa: BLE001 - failure evidence must not mask original error
            pass


def _validate_event(plan: ProjectionPlan, event: GmailHistoryEvent) -> None:
    if event.account_id != plan.account_id:
        raise MessageProjectionMaterializationError(
            "Projection event belongs to another Gmail account"
        )
    if event.processing_status is not HistoryProcessingStatus.PROCESSED:
        raise MessageProjectionMaterializationError(
            f"Projection requires committed PROCESSED history events: {event.event_id}"
        )
    try:
        input_history = int(plan.input_checkpoint_history_id)
        target_history = int(plan.projected_checkpoint_history_id)
        event_history = int(event.history_id)
    except ValueError as exc:
        raise MessageProjectionMaterializationError(
            "Projection History frontiers must be numeric Gmail history IDs"
        ) from exc
    if target_history < input_history:
        raise MessageProjectionMaterializationError(
            "Projected checkpoint must not precede input checkpoint"
        )
    if not input_history < event_history <= target_history:
        raise MessageProjectionMaterializationError(
            f"Event history id {event.history_id} is outside projection frontier "
            f"({plan.input_checkpoint_history_id}, {plan.projected_checkpoint_history_id}]"
        )


def _result(
    plan: ProjectionPlan,
    events: Sequence[GmailHistoryEvent],
    rows: dict[str, list[dict[str, Any]]],
    digest: str,
    *,
    duplicate_events_ignored: int,
    row_counts: dict[str, int] | None = None,
) -> MessageProjectionResult:
    unique: dict[str, GmailHistoryEvent] = {}
    for event in events:
        unique.setdefault(event.event_id, event)
    return MessageProjectionResult(
        projection_id=plan.projection_id,
        input_history_id=plan.input_checkpoint_history_id,
        projected_history_id=plan.projected_checkpoint_history_id,
        events_seen=len(events),
        duplicate_events_ignored=duplicate_events_ignored,
        messages_added=sum(
            event.event_type is GmailHistoryEventType.MESSAGE_ADDED
            for event in unique.values()
        ),
        messages_deleted=sum(
            event.event_type is GmailHistoryEventType.MESSAGE_DELETED
            for event in unique.values()
        ),
        logical_digest=digest,
        row_counts=row_counts
        or {name: len(rows[name]) for name in CURRENT_PROJECTION_TABLES},
        labels_added=sum(
            event.event_type is GmailHistoryEventType.LABEL_ADDED
            for event in unique.values()
        ),
        labels_removed=sum(
            event.event_type is GmailHistoryEventType.LABEL_REMOVED
            for event in unique.values()
        ),
    )


def _duplicate_count(events: Sequence[GmailHistoryEvent]) -> int:
    return len(events) - len({event.event_id for event in events})
