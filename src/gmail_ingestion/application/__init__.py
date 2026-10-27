"""Application use cases."""

from .authenticate import AuthenticateMailbox
from .bootstrap_projection_baseline import (
    LegacyBaselineBootstrapError,
    LegacyBaselineBootstrapResult,
    LegacyRawBaselineBootstrapper,
)
from .filtered_sync import FilteredMessageSnapshot
from .full_sync import FullMessageSnapshot, FullSnapshotResult
from .incremental_sync import (
    FullResyncRequiredError,
    IncrementalHistoryResult,
    IncrementalHistorySync,
    MissingCommittedCheckpointError,
)
from .labels import IngestLabels, LabelsIngestionResult
from .message_processing import MessagePostProcessor, MessageProcessingSummary
from .message_projection import (
    MessageDeltaProjectionService,
    MessageProjectionMaterializationError,
)
from .profile import GetMailboxProfile
from .project_current_state import ProjectCurrentState, ProjectCurrentStateResult
from .projection_reliability import ProjectionReliabilityResult, ProjectionReliabilityService
from .range_sync import RangeMessageSnapshot
from .reliable_incremental import (
    FullSnapshotRecoveryError,
    ReliableIncrementalResult,
    ReliableIncrementalSync,
)

__all__ = [
    "AuthenticateMailbox",
    "FilteredMessageSnapshot",
    "FullMessageSnapshot",
    "FullSnapshotResult",
    "FullResyncRequiredError",
    "FullSnapshotRecoveryError",
    "GetMailboxProfile",
    "LegacyBaselineBootstrapError",
    "LegacyBaselineBootstrapResult",
    "LegacyRawBaselineBootstrapper",
    "IncrementalHistoryResult",
    "IncrementalHistorySync",
    "IngestLabels",
    "LabelsIngestionResult",
    "MessageDeltaProjectionService",
    "MessagePostProcessor",
    "MessageProjectionMaterializationError",
    "MissingCommittedCheckpointError",
    "MessageProcessingSummary",
    "ProjectCurrentState",
    "ProjectCurrentStateResult",
    "ProjectionReliabilityResult",
    "ProjectionReliabilityService",
    "RangeMessageSnapshot",
    "ReliableIncrementalResult",
    "ReliableIncrementalSync",
]
