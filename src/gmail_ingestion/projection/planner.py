"""Deterministic projection planning."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from .models import CurrentPointer, ProjectionIdentity, ProjectionPlan


class ProjectionPlanningError(ValueError):
    """Raised when a projection cannot be planned from the current control-plane state."""


class ProjectionPlanner:
    """Build immutable plans without reading Gmail or mutating persisted projection state."""

    def plan(
        self,
        *,
        account_id: str,
        baseline_run_id: UUID,
        baseline_dataset_version: str,
        input_checkpoint_history_id: str,
        projected_checkpoint_history_id: str,
        current_pointer: CurrentPointer | None,
        created_at: datetime | None = None,
    ) -> ProjectionPlan:
        """Create a deterministic plan anchored to the currently published projection."""
        predecessor_projection_id: str | None = None
        if current_pointer is not None:
            if current_pointer.account_id != account_id:
                raise ProjectionPlanningError(
                    "current projection pointer belongs to another Gmail account"
                )
            if current_pointer.projected_checkpoint_history_id != input_checkpoint_history_id:
                raise ProjectionPlanningError(
                    "projection input checkpoint must equal the current projection frontier"
                )
            predecessor_projection_id = current_pointer.projection_id

        identity = ProjectionIdentity(
            account_id=account_id,
            baseline_run_id=baseline_run_id,
            baseline_dataset_version=baseline_dataset_version,
            projected_checkpoint_history_id=projected_checkpoint_history_id,
        )
        return ProjectionPlan(
            account_id=account_id,
            projection_id=identity.projection_id,
            baseline_run_id=baseline_run_id,
            baseline_dataset_version=baseline_dataset_version,
            input_checkpoint_history_id=input_checkpoint_history_id,
            projected_checkpoint_history_id=projected_checkpoint_history_id,
            predecessor_projection_id=predecessor_projection_id,
            created_at=created_at or datetime.now(UTC),
        )
