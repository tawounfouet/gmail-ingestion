"""Projection control-plane validation for the 0.5.0a1 foundation."""

from __future__ import annotations

from .models import ProjectionCandidate, ProjectionStatus


class ProjectionValidationError(ValueError):
    """Raised when a candidate cannot safely advance to READY."""


class ProjectionValidator:
    """Validate structural invariants before a candidate becomes promotable."""

    def validate_for_ready(self, candidate: ProjectionCandidate) -> None:
        """Validate the alpha control-plane contract without applying business deltas."""
        if candidate.state.status is not ProjectionStatus.BUILDING:
            raise ProjectionValidationError(
                "projection candidate must be BUILDING before validation"
            )
        if candidate.manifest.status is not ProjectionStatus.BUILDING:
            raise ProjectionValidationError(
                "projection manifest must be BUILDING before validation"
            )
        if candidate.plan.projected_checkpoint_history_id != (
            candidate.state.projected_checkpoint_history_id
        ):
            raise ProjectionValidationError("projection frontier changed during candidate build")
