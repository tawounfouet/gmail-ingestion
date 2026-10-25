"""Gmail domain models."""

from .account import GmailAccount
from .attachment import AttachmentCandidate, AttachmentDownloadStatus, AttachmentRecord
from .checkpoint import CHECKPOINT_SCHEMA_VERSION, CheckpointState, GmailCheckpoint
from .filter import DateRange, GmailFilter, ResolvedDateRange
from .history import (
    GmailHistoryEvent,
    GmailHistoryEventType,
    GmailHistoryMappingError,
    map_message_added_events,
)
from .label import GmailLabel
from .message import MessageDiscoveryRecord, MessageFetchStatus

__all__ = [
    "AttachmentCandidate",
    "AttachmentDownloadStatus",
    "AttachmentRecord",
    "CHECKPOINT_SCHEMA_VERSION",
    "CheckpointState",
    "DateRange",
    "GmailAccount",
    "GmailCheckpoint",
    "GmailFilter",
    "GmailHistoryEvent",
    "GmailHistoryEventType",
    "GmailHistoryMappingError",
    "GmailLabel",
    "MessageDiscoveryRecord",
    "map_message_added_events",
    "ResolvedDateRange",
    "MessageFetchStatus",
]
