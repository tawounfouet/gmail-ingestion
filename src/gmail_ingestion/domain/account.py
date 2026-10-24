"""Domain model for a Gmail account."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GmailAccount:
    """Authenticated Gmail mailbox identity and source profile counters."""

    account_id: str
    email_address: str
    messages_total: int | None
    threads_total: int | None
    history_id: str | None
