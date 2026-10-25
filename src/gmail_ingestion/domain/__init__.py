"""Gmail domain models."""

from .account import GmailAccount
from .attachment import AttachmentCandidate, AttachmentDownloadStatus, AttachmentRecord
from .filter import DateRange, GmailFilter, ResolvedDateRange
from .label import GmailLabel
from .message import MessageDiscoveryRecord, MessageFetchStatus

__all__ = [
    "AttachmentCandidate",
    "AttachmentDownloadStatus",
    "AttachmentRecord",
    "DateRange",
    "GmailAccount",
    "GmailFilter",
    "GmailLabel",
    "MessageDiscoveryRecord",
    "ResolvedDateRange",
    "MessageFetchStatus",
]
