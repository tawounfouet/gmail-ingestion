"""Application use cases."""

from .authenticate import AuthenticateMailbox
from .labels import IngestLabels, LabelsIngestionResult
from .profile import GetMailboxProfile

__all__ = [
    "AuthenticateMailbox",
    "GetMailboxProfile",
    "IngestLabels",
    "LabelsIngestionResult",
]
