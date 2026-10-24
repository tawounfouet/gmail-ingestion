"""Domain model for a normalized Gmail label."""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class GmailLabel:
    """One normalized Gmail label for one account."""

    account_id: str
    label_id: str
    name: str
    label_type: str | None
    message_list_visibility: str | None
    label_list_visibility: str | None
    messages_total: int | None
    messages_unread: int | None
    threads_total: int | None
    threads_unread: int | None
    color_text: str | None
    color_background: str | None
    is_system: bool
    ingested_at: datetime

    def to_row(self) -> dict[str, Any]:
        """Return a mutable tabular row for PyIngestKit Dataset."""
        return asdict(self)
