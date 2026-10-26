"""Application-level projection reliability and rebuild qualification."""

from __future__ import annotations

from dataclasses import dataclass

from gmail_ingestion.application.message_projection import MessageDeltaProjectionService
from gmail_ingestion.projection.message_delta import MessageProjectionResult
from gmail_ingestion.projection.models import ProjectionPlan
from gmail_ingestion.projection.reliability import (
    ProjectionConvergenceError,
    ProjectionConvergenceResult,
    ProjectionDeltaBatch,
    ProjectionGapError,
    validate_delta_chain,
)


@dataclass(frozen=True, slots=True)
class ProjectionReliabilityResult:
    """Combined incremental materialization and deterministic rebuild evidence."""

    materialization: MessageProjectionResult
    convergence: ProjectionConvergenceResult


class ProjectionReliabilityService:
    """Validate delta continuity and prove incremental/current-state rebuild convergence."""

    def __init__(self, materializer: MessageDeltaProjectionService) -> None:
        self.materializer = materializer

    def materialize_incremental(
        self,
        plan: ProjectionPlan,
        batches: tuple[ProjectionDeltaBatch, ...],
    ) -> MessageProjectionResult:
        """Validate current-frontier continuity then materialize only the new delta chain."""
        events = validate_delta_chain(
            account_id=plan.account_id,
            start_history_id=plan.input_checkpoint_history_id,
            target_history_id=plan.projected_checkpoint_history_id,
            batches=batches,
        )
        return self.materializer.materialize(plan, events)

    def qualify_rebuild(
        self,
        plan: ProjectionPlan,
        incremental: MessageProjectionResult,
        rebuild_batches: tuple[ProjectionDeltaBatch, ...],
    ) -> ProjectionConvergenceResult:
        """Rebuild from baseline and assert logical equality with the incremental projection."""
        baseline = self.materializer.baseline_reader.read(
            plan.baseline_run_id,
            plan.account_id,
        )
        baseline_history_id = baseline.account.history_id
        if baseline_history_id is None:
            raise ProjectionGapError(
                "normalized baseline must expose its Gmail history frontier for rebuild qualification"
            )

        rebuild_events = validate_delta_chain(
            account_id=plan.account_id,
            start_history_id=baseline_history_id,
            target_history_id=plan.projected_checkpoint_history_id,
            batches=rebuild_batches,
        )
        rebuild_digest = self.materializer.rebuild_digest(
            plan,
            rebuild_events,
            start_history_id=baseline_history_id,
        )
        evidence = ProjectionConvergenceResult(
            projection_id=plan.projection_id,
            target_history_id=plan.projected_checkpoint_history_id,
            incremental_digest=incremental.logical_digest,
            rebuild_digest=rebuild_digest,
            equal=incremental.logical_digest == rebuild_digest,
            incremental_batch_count=0,
            rebuild_batch_count=len(rebuild_batches),
        )
        if not evidence.equal:
            raise ProjectionConvergenceError(
                "incremental projection does not equal deterministic full rebuild: "
                f"{evidence.incremental_digest} != {evidence.rebuild_digest}"
            )
        return evidence

    def materialize_and_qualify(
        self,
        plan: ProjectionPlan,
        *,
        incremental_batches: tuple[ProjectionDeltaBatch, ...],
        rebuild_batches: tuple[ProjectionDeltaBatch, ...],
    ) -> ProjectionReliabilityResult:
        """Materialize the new frontier and immediately prove it against a full rebuild."""
        incremental = self.materialize_incremental(plan, incremental_batches)
        convergence = self.qualify_rebuild(plan, incremental, rebuild_batches)
        convergence = ProjectionConvergenceResult(
            projection_id=convergence.projection_id,
            target_history_id=convergence.target_history_id,
            incremental_digest=convergence.incremental_digest,
            rebuild_digest=convergence.rebuild_digest,
            equal=convergence.equal,
            incremental_batch_count=len(incremental_batches),
            rebuild_batch_count=convergence.rebuild_batch_count,
        )
        return ProjectionReliabilityResult(
            materialization=incremental,
            convergence=convergence,
        )
