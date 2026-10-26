"""Projection and current-state materialization control-plane primitives."""

from .digest import logical_projection_digest
from .message_delta import (
    CURRENT_PROJECTION_TABLES,
    MESSAGE_PROJECTION_TABLES,
    MessageProjectionResult,
    PreparedMessageDelta,
)
from .message_reducer import MessageProjectionReducer, MessageProjectionReductionError
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
from .reliability import (
    ProjectionConvergenceError,
    ProjectionConvergenceResult,
    ProjectionDeltaBatch,
    ProjectionGapError,
    validate_delta_chain,
)
from .service import ProjectionFoundationService
from .thread_reducer import ThreadProjectionError, recompute_threads
from .validator import ProjectionValidationError, ProjectionValidator

__all__ = [
    "CURRENT_PROJECTION_TABLES",
    "MESSAGE_PROJECTION_TABLES",
    "PROJECTION_SCHEMA_VERSION",
    "CurrentPointer",
    "MessageProjectionReducer",
    "MessageProjectionReductionError",
    "MessageProjectionResult",
    "ProjectionCandidate",
    "ProjectionConvergenceError",
    "ProjectionConvergenceResult",
    "ProjectionDeltaBatch",
    "ProjectionFoundationService",
    "ProjectionGapError",
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
    "ThreadProjectionError",
    "PreparedMessageDelta",
    "logical_projection_digest",
    "recompute_threads",
    "validate_delta_chain",
]
