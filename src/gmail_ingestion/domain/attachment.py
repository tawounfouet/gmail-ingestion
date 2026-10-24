"""Domain objects for Gmail attachment handling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class AttachmentDownloadStatus(StrEnum):
    """Lifecycle state for an attachment within one snapshot run."""

    METADATA_ONLY = "METADATA_ONLY"
    DOWNLOADED = "DOWNLOADED"
    SKIPPED_SIZE_LIMIT = "SKIPPED_SIZE_LIMIT"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class AttachmentCandidate:
    """Attachment-like MIME part discovered without network side effects."""

    attachment_id: str | None
    part_id: str
    part_path: str
    filename: str
    mime_type: str
    size_bytes: int
    is_inline: bool
    content_id: str | None
    inline_data: str | None = None


@dataclass(frozen=True, slots=True)
class AttachmentRecord:
    """Auditable attachment metadata after optional binary acquisition."""

    attachment_id: str | None
    part_id: str
    part_path: str
    filename: str
    mime_type: str
    size_bytes: int
    content_hash_sha256: str | None
    storage_uri: str | None
    download_status: AttachmentDownloadStatus
    downloaded_at: datetime | None
    is_inline: bool
    content_id: str | None
    fetch_attempts: int = 0
    error_code: str | None = None
    error_message: str | None = None
