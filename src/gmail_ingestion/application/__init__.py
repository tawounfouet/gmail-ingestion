"""Application use cases."""

from .authenticate import AuthenticateMailbox
from .full_sync import FullMessageSnapshot, FullSnapshotResult
from .labels import IngestLabels, LabelsIngestionResult
from .message_processing import MessagePostProcessor, MessageProcessingSummary
from .profile import GetMailboxProfile

__all__ = [
    "AuthenticateMailbox",
    "FullMessageSnapshot",
    "FullSnapshotResult",
    "GetMailboxProfile",
    "IngestLabels",
    "LabelsIngestionResult",
    "MessagePostProcessor",
    "MessageProcessingSummary",
]
