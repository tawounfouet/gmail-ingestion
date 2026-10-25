"""Gmail domain models."""

from .account import GmailAccount
from .attachment import AttachmentCandidate, AttachmentDownloadStatus, AttachmentRecord
from .checkpoint import CHECKPOINT_SCHEMA_VERSION, CheckpointState, GmailCheckpoint
from .filter import DateRange, GmailFilter, ResolvedDateRange
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
    "GmailLabel",
    "MessageDiscoveryRecord",
    "ResolvedDateRange",
    "MessageFetchStatus",
]
