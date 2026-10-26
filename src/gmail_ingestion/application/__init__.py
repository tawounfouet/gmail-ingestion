"""Application use cases."""

from .authenticate import AuthenticateMailbox
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
    "IncrementalHistoryResult",
    "IncrementalHistorySync",
    "IngestLabels",
    "LabelsIngestionResult",
    "MessageDeltaProjectionService",
    "MessagePostProcessor",
    "MessageProjectionMaterializationError",
    "MissingCommittedCheckpointError",
    "MessageProcessingSummary",
    "RangeMessageSnapshot",
    "ReliableIncrementalResult",
    "ReliableIncrementalSync",
]
