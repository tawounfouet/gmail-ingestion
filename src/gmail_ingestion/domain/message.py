"""Domain objects for Gmail message discovery and hydration control."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class MessageFetchStatus(StrEnum):
    """Lifecycle state for a discovered Gmail message."""

    PENDING = "PENDING"
    FETCHED = "FETCHED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class MessageDiscoveryRecord:
    """One message reference discovered during a snapshot run."""

    run_id: str
    account_id: str
    message_id: str
    thread_id: str
    page_index: int
    discovered_at: datetime
    fetch_status: MessageFetchStatus = MessageFetchStatus.PENDING
    fetch_attempts: int = 0
    last_fetch_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
