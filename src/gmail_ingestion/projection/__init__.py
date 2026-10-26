"""Projection and current-state materialization control-plane primitives."""

from .models import (
    PROJECTION_SCHEMA_VERSION,
    CurrentPointer,
    ProjectionCandidate,
    ProjectionIdentity,
    ProjectionManifest,
    ProjectionPlan,
    ProjectionState,
    ProjectionStatus,
)
from .planner import ProjectionPlanner, ProjectionPlanningError
from .ports import ProjectionStore
from .service import ProjectionFoundationService
from .validator import ProjectionValidationError, ProjectionValidator

__all__ = [
    "PROJECTION_SCHEMA_VERSION",
    "CurrentPointer",
    "ProjectionCandidate",
    "ProjectionFoundationService",
    "ProjectionIdentity",
    "ProjectionManifest",
    "ProjectionPlan",
    "ProjectionPlanner",
    "ProjectionPlanningError",
    "ProjectionState",
    "ProjectionStore",
    "ProjectionStatus",
    "ProjectionValidationError",
    "ProjectionValidator",
]
