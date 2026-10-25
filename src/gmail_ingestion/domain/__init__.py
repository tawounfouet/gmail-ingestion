"""Gmail domain models."""

from .account import GmailAccount
from .attachment import AttachmentCandidate, AttachmentDownloadStatus, AttachmentRecord
from .filter import GmailFilter
from .label import GmailLabel
from .message import MessageDiscoveryRecord, MessageFetchStatus

__all__ = [
    "AttachmentCandidate",
    "AttachmentDownloadStatus",
    "AttachmentRecord",
    "GmailAccount",
    "GmailFilter",
    "GmailLabel",
    "MessageDiscoveryRecord",
    "MessageFetchStatus",
]
