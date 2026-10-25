"""Shared Gmail API exceptions."""


class GmailApiError(RuntimeError):
    """Safe connector-level Gmail API error."""


class GmailMessageNotFoundError(GmailApiError):
    """Raised when a Gmail message disappears before it can be hydrated."""
