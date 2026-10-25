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
from .profile import GetMailboxProfile
from .range_sync import RangeMessageSnapshot

__all__ = [
    "AuthenticateMailbox",
    "FilteredMessageSnapshot",
    "FullMessageSnapshot",
    "FullSnapshotResult",
    "FullResyncRequiredError",
    "GetMailboxProfile",
    "IncrementalHistoryResult",
    "IncrementalHistorySync",
    "IngestLabels",
    "LabelsIngestionResult",
    "MessagePostProcessor",
    "MissingCommittedCheckpointError",
    "MessageProcessingSummary",
    "RangeMessageSnapshot",
]
