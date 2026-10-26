"""Application service for the projection candidate lifecycle introduced in 0.5.0a1."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from .models import (
    CurrentPointer,
    ProjectionCandidate,
    ProjectionManifest,
    ProjectionPlan,
    ProjectionState,
)
from .planner import ProjectionPlanner
from .ports import ProjectionStore
from .validator import ProjectionValidator


class ProjectionFoundationService:
    """Orchestrate plan -> candidate -> build -> validate -> atomic promotion."""

    def __init__(
        self,
        store: ProjectionStore,
        *,
        planner: ProjectionPlanner | None = None,
        validator: ProjectionValidator | None = None,
    ) -> None:
        self.store = store
        self.planner = planner or ProjectionPlanner()
        self.validator = validator or ProjectionValidator()

    def plan(
        self,
        *,
        account_id: str,
        baseline_run_id: UUID,
        baseline_dataset_version: str,
        input_checkpoint_history_id: str,
        projected_checkpoint_history_id: str,
        created_at: datetime | None = None,
    ) -> ProjectionPlan:
        """Plan one deterministic projection from the current published frontier."""
        current = self.store.load_current(account_id)
        return self.planner.plan(
            account_id=account_id,
            baseline_run_id=baseline_run_id,
            baseline_dataset_version=baseline_dataset_version,
            input_checkpoint_history_id=input_checkpoint_history_id,
            projected_checkpoint_history_id=projected_checkpoint_history_id,
            current_pointer=current,
            created_at=created_at,
        )

    def create_candidate(
        self,
        plan: ProjectionPlan,
        *,
        created_at: datetime | None = None,
    ) -> ProjectionCandidate:
        """Persist a NEW candidate without changing the authoritative current pointer."""
        now = created_at or datetime.now(UTC)
        candidate = ProjectionCandidate(
            plan=plan,
            state=ProjectionState.new(plan, created_at=now),
            manifest=ProjectionManifest.from_plan(plan, created_at=now),
        )
        self.store.save_candidate(candidate)
        return candidate

    def begin_build(
        self,
        account_id: str,
        projection_id: str,
        *,
        started_at: datetime | None = None,
    ) -> ProjectionCandidate:
        """Move a persisted candidate into BUILDING state."""
        candidate = self.store.require_candidate(account_id, projection_id)
        now = started_at or datetime.now(UTC)
        updated = ProjectionCandidate(
            plan=candidate.plan,
            state=candidate.state.start_build(started_at=now),
            manifest=candidate.manifest.start_build(started_at=now),
        )
        self.store.update_candidate(updated, expected_status=candidate.state.status)
        return updated

    def validate_candidate(
        self,
        account_id: str,
        projection_id: str,
        *,
        logical_digest: str | None = None,
        validated_at: datetime | None = None,
    ) -> ProjectionCandidate:
        """Validate control-plane invariants and mark the candidate READY for promotion."""
        candidate = self.store.require_candidate(account_id, projection_id)
        self.validator.validate_for_ready(candidate)
        now = validated_at or datetime.now(UTC)
        updated = ProjectionCandidate(
            plan=candidate.plan,
            state=candidate.state.mark_ready(
                logical_digest=logical_digest,
                ready_at=now,
            ),
            manifest=candidate.manifest.mark_ready(
                logical_digest=logical_digest,
                validated_at=now,
            ),
        )
        self.store.update_candidate(updated, expected_status=candidate.state.status)
        return updated

    def fail_candidate(
        self,
        account_id: str,
        projection_id: str,
        reason: str,
        *,
        failed_at: datetime | None = None,
    ) -> ProjectionCandidate:
        """Persist an explicit FAILED candidate without changing the current pointer."""
        candidate = self.store.require_candidate(account_id, projection_id)
        now = failed_at or datetime.now(UTC)
        updated = ProjectionCandidate(
            plan=candidate.plan,
            state=candidate.state.fail(reason, failed_at=now),
            manifest=candidate.manifest.fail(reason, failed_at=now),
        )
        self.store.update_candidate(updated, expected_status=candidate.state.status)
        return updated

    def promote(
        self,
        account_id: str,
        projection_id: str,
        *,
        promoted_at: datetime | None = None,
    ) -> CurrentPointer:
        """Atomically publish a READY candidate with stale-writer protection."""
        return self.store.promote(
            account_id,
            projection_id,
            promoted_at=promoted_at,
        )
