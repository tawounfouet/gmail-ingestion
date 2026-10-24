"""Application use cases."""

from .authenticate import AuthenticateMailbox
from .full_sync import FullMessageSnapshot, FullSnapshotResult
from .labels import IngestLabels, LabelsIngestionResult
from .profile import GetMailboxProfile

__all__ = [
    "AuthenticateMailbox",
    "FullMessageSnapshot",
    "FullSnapshotResult",
    "GetMailboxProfile",
    "IngestLabels",
    "LabelsIngestionResult",
]
