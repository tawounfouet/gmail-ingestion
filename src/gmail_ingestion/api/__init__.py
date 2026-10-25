"""Gmail API adapters."""

from .attachments import GmailAttachmentsApi
from .client import GmailApiClient
from .client_error import GmailApiError
from .history import GmailHistoryApi, GmailHistoryExpiredError
from .labels import GmailLabelsApi
from .messages import GmailMessagesApi
from .profile import GmailProfileError, map_gmail_profile

__all__ = [
    "GmailApiClient",
    "GmailApiError",
    "GmailAttachmentsApi",
    "GmailHistoryApi",
    "GmailHistoryExpiredError",
    "GmailLabelsApi",
    "GmailMessagesApi",
    "GmailProfileError",
    "map_gmail_profile",
]
