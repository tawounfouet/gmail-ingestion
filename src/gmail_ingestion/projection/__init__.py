"""Projection and current-state materialization control-plane primitives."""

from .digest import logical_projection_digest
from .message_delta import (
    MESSAGE_PROJECTION_TABLES,
    MessageProjectionResult,
    PreparedMessageDelta,
)
from .message_reducer import MessageProjectionReducer, MessageProjectionReductionError
from .message_service import MessageDeltaProjectionService, MessageProjectionMaterializationError
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
    "MESSAGE_PROJECTION_TABLES",
    "PROJECTION_SCHEMA_VERSION",
    "CurrentPointer",
    "MessageDeltaProjectionService",
    "MessageProjectionMaterializationError",
    "MessageProjectionReducer",
    "MessageProjectionReductionError",
    "MessageProjectionResult",
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
    "PreparedMessageDelta",
    "logical_projection_digest",
]
