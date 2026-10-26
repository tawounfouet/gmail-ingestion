"""Operational projection use case backed by committed local 0.4 artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.persistence.projection_batch_reader import LocalProjectionBatchReader
from gmail_ingestion.persistence.projection_store import LocalProjectionStore
from gmail_ingestion.projection import (
    ProjectionGapError,
    validate_delta_chain,
)

from .message_projection import MessageDeltaProjectionService
from .projection_reliability import ProjectionReliabilityService


@dataclass(frozen=True, slots=True)
class ProjectCurrentStateResult:
    """User-facing outcome of one projection orchestration."""

    account_id: str
    projection_id: str
    input_history_id: str
    target_history_id: str
    logical_digest: str
    rebuild_digest: str
    incremental_batch_count: int
    rebuild_batch_count: int
    promoted: bool
    already_current: bool


class ProjectCurrentState:
    """Project committed Gmail artifacts into a deterministic current-state dataset."""

    def __init__(
        self,
        artifact_store: LocalArtifactStore,
        projection_store: LocalProjectionStore,
        *,
        batch_reader: LocalProjectionBatchReader | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.projection_store = projection_store
        self.batch_reader = batch_reader or LocalProjectionBatchReader(artifact_store)
        self.materializer = MessageDeltaProjectionService(artifact_store, projection_store)
        self.reliability = ProjectionReliabilityService(self.materializer)

    def run(
        self,
        *,
        account_id: str,
        baseline_run_id: UUID,
        baseline_dataset_version: str,
        delta_run_ids: tuple[UUID, ...],
        candidate_only: bool = False,
    ) -> ProjectCurrentStateResult:
        """Materialize the latest explicit delta frontier and prove rebuild convergence."""
        baseline = self.materializer.baseline_reader.read(baseline_run_id, account_id)
        baseline_history_id = baseline.account.history_id
        if baseline_history_id is None:
            raise ProjectionGapError(
                "normalized baseline must expose its Gmail history frontier"
            )

        rebuild_batches = self.batch_reader.read_many(delta_run_ids)
        target_history_id = (
            rebuild_batches[-1].output_history_id
            if rebuild_batches
            else baseline_history_id
        )
        validate_delta_chain(
            account_id=account_id,
            start_history_id=baseline_history_id,
            target_history_id=target_history_id,
            batches=rebuild_batches,
        )

        current = self.projection_store.load_current(account_id)
        if current is not None:
            if current.baseline_run_id != baseline_run_id:
                raise ProjectionGapError(
                    "current projection was built from another normalized baseline"
                )
            if current.baseline_dataset_version != baseline_dataset_version:
                raise ProjectionGapError(
                    "current projection baseline dataset version does not match request"
                )
            if int(target_history_id) < int(current.projected_checkpoint_history_id):
                raise ProjectionGapError(
                    "requested target precedes the current projection frontier"
                )
            if target_history_id == current.projected_checkpoint_history_id:
                version = self.projection_store.load_version(
                    account_id,
                    current.projection_id,
                )
                if version is None or version.state.logical_digest is None:
                    raise ProjectionGapError(
                        "current projection has no immutable qualified version evidence"
                    )
                return ProjectCurrentStateResult(
                    account_id=account_id,
                    projection_id=current.projection_id,
                    input_history_id=current.projected_checkpoint_history_id,
                    target_history_id=target_history_id,
                    logical_digest=version.state.logical_digest,
                    rebuild_digest=version.state.logical_digest,
                    incremental_batch_count=0,
                    rebuild_batch_count=len(rebuild_batches),
                    promoted=True,
                    already_current=True,
                )
            input_history_id = current.projected_checkpoint_history_id
            incremental_batches = _suffix_from_frontier(
                rebuild_batches,
                input_history_id,
            )
        else:
            input_history_id = baseline_history_id
            incremental_batches = rebuild_batches

        plan = self.materializer.foundation.plan(
            account_id=account_id,
            baseline_run_id=baseline_run_id,
            baseline_dataset_version=baseline_dataset_version,
            input_checkpoint_history_id=input_history_id,
            projected_checkpoint_history_id=target_history_id,
        )
        qualification = self.reliability.materialize_and_qualify(
            plan,
            incremental_batches=incremental_batches,
            rebuild_batches=rebuild_batches,
        )

        if candidate_only:
            promoted = False
        else:
            self.materializer.foundation.promote(
                account_id,
                plan.projection_id,
            )
            promoted = True

        return ProjectCurrentStateResult(
            account_id=account_id,
            projection_id=plan.projection_id,
            input_history_id=input_history_id,
            target_history_id=target_history_id,
            logical_digest=qualification.convergence.incremental_digest,
            rebuild_digest=qualification.convergence.rebuild_digest,
            incremental_batch_count=qualification.convergence.incremental_batch_count,
            rebuild_batch_count=qualification.convergence.rebuild_batch_count,
            promoted=promoted,
            already_current=False,
        )


def _suffix_from_frontier(
    batches: tuple[object, ...],
    frontier: str,
) -> tuple[object, ...]:
    for index, batch in enumerate(batches):
        input_history_id = getattr(batch, "input_history_id", None)
        if input_history_id == frontier:
            return batches[index:]
    raise ProjectionGapError(
        f"no committed delta batch continues current projection frontier {frontier!r}"
    )
