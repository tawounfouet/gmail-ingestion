"""Post-hydration MIME analysis and optional attachment acquisition."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import UUID

from pyingestkit.artifacts.base import ArtifactStore
from pyingestkit.core.exceptions import StorageError
from pyingestkit.retry import RetryPolicy

from gmail_ingestion.api import GmailApiError
from gmail_ingestion.domain import (
    AttachmentCandidate,
    AttachmentDownloadStatus,
    AttachmentRecord,
    MessageFetchStatus,
)
from gmail_ingestion.manifest import ManifestIssue
from gmail_ingestion.normalization import MimeParseError, analyze_message
from gmail_ingestion.normalization.mime_walker import decode_base64url
from gmail_ingestion.persistence import (
    AttachmentBinaryWriter,
    MessageAnalysisWriter,
    RawMessageWriter,
)


class AttachmentsApiProtocol(Protocol):
    def get_attachment(self, message_id: str, attachment_id: str) -> Mapping[str, Any]: ...


class AttachmentClientProtocol(Protocol):
    attachments: AttachmentsApiProtocol


@dataclass(frozen=True, slots=True)
class MessageProcessingSummary:
    """Aggregate MIME/attachment metrics for one snapshot run."""

    mime_messages_processed: int
    mime_messages_failed: int
    mime_parts: int
    attachments_detected: int
    attachments_fetched: int
    attachments_failed: int
    attachments_skipped: int
    bytes_downloaded: int
    errors: tuple[ManifestIssue, ...]
    warnings: tuple[ManifestIssue, ...]


class MessagePostProcessor:
    """Derive usable MIME artifacts and optionally persist attachment binaries."""

    def __init__(
        self,
        store: ArtifactStore,
        client: AttachmentClientProtocol,
        retry: RetryPolicy,
        *,
        download_attachments: bool,
        max_attachment_bytes: int | None,
    ) -> None:
        self.store = store
        self.client = client
        self.retry = retry
        self.download_attachments = download_attachments
        self.max_attachment_bytes = max_attachment_bytes
        self.raw_messages = RawMessageWriter(store)
        self.analysis_writer = MessageAnalysisWriter(store)
        self.binary_writer = AttachmentBinaryWriter(store)

    def process(
        self,
        run_id: UUID,
        account_id: str,
        records: Mapping[str, Any],
    ) -> MessageProcessingSummary:
        mime_messages_processed = 0
        mime_messages_failed = 0
        mime_parts = 0
        attachments_detected = 0
        attachments_fetched = 0
        attachments_failed = 0
        attachments_skipped = 0
        bytes_downloaded = 0
        errors: list[ManifestIssue] = []
        warnings: list[ManifestIssue] = []

        for message_id, discovery in records.items():
            if discovery.fetch_status is not MessageFetchStatus.FETCHED:
                continue
            try:
                message = self.raw_messages.read(run_id, message_id)
                analysis = analyze_message(message)
            except (MimeParseError, StorageError, ValueError) as exc:
                mime_messages_failed += 1
                errors.append(
                    ManifestIssue(
                        scope="mime",
                        code="MIME_ANALYSIS_ERROR",
                        message=str(exc),
                        message_id=message_id,
                    )
                )
                continue

            attachment_records: list[AttachmentRecord] = []
            for candidate in analysis.attachments:
                record, issue = self._handle_attachment(run_id, message_id, candidate)
                attachment_records.append(record)
                attachments_detected += 1
                if record.download_status is AttachmentDownloadStatus.DOWNLOADED:
                    attachments_fetched += 1
                    bytes_downloaded += record.size_bytes
                elif record.download_status is AttachmentDownloadStatus.FAILED:
                    attachments_failed += 1
                elif record.download_status is AttachmentDownloadStatus.SKIPPED_SIZE_LIMIT:
                    attachments_skipped += 1
                if issue is not None:
                    target = errors if record.download_status is AttachmentDownloadStatus.FAILED else warnings
                    target.append(issue)

            for warning in analysis.warnings:
                warnings.append(
                    ManifestIssue(
                        scope="mime",
                        code="MIME_WARNING",
                        message=warning,
                        message_id=message_id,
                    )
                )

            self.analysis_writer.write(
                run_id,
                account_id,
                message_id,
                analysis,
                tuple(attachment_records),
            )
            mime_messages_processed += 1
            mime_parts += len(analysis.parts)

        return MessageProcessingSummary(
            mime_messages_processed=mime_messages_processed,
            mime_messages_failed=mime_messages_failed,
            mime_parts=mime_parts,
            attachments_detected=attachments_detected,
            attachments_fetched=attachments_fetched,
            attachments_failed=attachments_failed,
            attachments_skipped=attachments_skipped,
            bytes_downloaded=bytes_downloaded,
            errors=tuple(errors),
            warnings=tuple(warnings),
        )

    def _handle_attachment(
        self,
        run_id: UUID,
        message_id: str,
        candidate: AttachmentCandidate,
    ) -> tuple[AttachmentRecord, ManifestIssue | None]:
        if not self.download_attachments:
            return _record(candidate, AttachmentDownloadStatus.METADATA_ONLY), None

        if (
            self.max_attachment_bytes is not None
            and candidate.size_bytes > self.max_attachment_bytes
        ):
            issue = ManifestIssue(
                scope="attachment",
                code="ATTACHMENT_SIZE_LIMIT",
                message=(
                    f"Attachment size {candidate.size_bytes} exceeds configured limit "
                    f"{self.max_attachment_bytes}"
                ),
                message_id=message_id,
                attachment_id=candidate.attachment_id,
            )
            return (
                _record(candidate, AttachmentDownloadStatus.SKIPPED_SIZE_LIMIT),
                issue,
            )

        existing = self.binary_writer.read_existing(run_id, message_id, candidate)
        if existing is not None:
            return (
                _record(
                    candidate,
                    AttachmentDownloadStatus.DOWNLOADED,
                    data=existing.data,
                    storage_uri=existing.storage_uri,
                ),
                None,
            )

        try:
            data, attempts = self._acquire_content(message_id, candidate)
        except AttachmentFetchFailure as exc:
            issue = ManifestIssue(
                scope="attachment",
                code=exc.code,
                message=str(exc),
                message_id=message_id,
                attachment_id=candidate.attachment_id,
            )
            return (
                _record(
                    candidate,
                    AttachmentDownloadStatus.FAILED,
                    fetch_attempts=exc.attempts,
                    error_code=exc.code,
                    error_message=str(exc),
                ),
                issue,
            )

        if self.max_attachment_bytes is not None and len(data) > self.max_attachment_bytes:
            issue = ManifestIssue(
                scope="attachment",
                code="ATTACHMENT_SIZE_LIMIT",
                message=(
                    f"Decoded attachment size {len(data)} exceeds configured limit "
                    f"{self.max_attachment_bytes}"
                ),
                message_id=message_id,
                attachment_id=candidate.attachment_id,
            )
            return (
                _record(
                    candidate,
                    AttachmentDownloadStatus.SKIPPED_SIZE_LIMIT,
                    data=data,
                    fetch_attempts=attempts,
                ),
                issue,
            )

        artifact = self.binary_writer.write(run_id, message_id, candidate, data)
        return (
            _record(
                candidate,
                AttachmentDownloadStatus.DOWNLOADED,
                data=data,
                storage_uri=str(artifact.location_uri),
                fetch_attempts=attempts,
            ),
            None,
        )

    def _acquire_content(
        self,
        message_id: str,
        candidate: AttachmentCandidate,
    ) -> tuple[bytes, int]:
        if candidate.inline_data is not None:
            try:
                return decode_base64url(candidate.inline_data), 0
            except MimeParseError as exc:
                raise AttachmentFetchFailure(
                    "ATTACHMENT_INLINE_DECODE_ERROR",
                    "Unable to decode inline Gmail attachment content",
                    0,
                ) from exc

        if candidate.attachment_id is None:
            raise AttachmentFetchFailure(
                "ATTACHMENT_CONTENT_UNAVAILABLE",
                "Attachment-like MIME part has neither attachmentId nor inline data",
                0,
            )

        attempts = 0

        def operation() -> Mapping[str, Any]:
            nonlocal attempts
            attempts += 1
            return self.client.attachments.get_attachment(
                message_id,
                candidate.attachment_id or "",
            )

        try:
            payload = self.retry.execute(
                operation,
                should_retry=lambda exc: isinstance(exc, GmailApiError),
            )
        except GmailApiError as exc:
            raise AttachmentFetchFailure(
                "GMAIL_ATTACHMENT_API_ERROR",
                (
                    f"Unable to fetch Gmail attachment {candidate.attachment_id!r} "
                    f"after {attempts} attempts"
                ),
                attempts,
            ) from exc

        data = payload.get("data")
        if not isinstance(data, str):
            raise AttachmentFetchFailure(
                "ATTACHMENT_DATA_MISSING",
                "Gmail attachment response contains no base64url data",
                attempts,
            )
        try:
            return decode_base64url(data), attempts
        except MimeParseError as exc:
            raise AttachmentFetchFailure(
                "ATTACHMENT_DECODE_ERROR",
                "Unable to decode Gmail attachment base64url data",
                attempts,
            ) from exc


class AttachmentFetchFailure(RuntimeError):
    """Safe per-attachment failure that does not abort the entire snapshot."""

    def __init__(self, code: str, message: str, attempts: int) -> None:
        super().__init__(message)
        self.code = code
        self.attempts = attempts


def _record(
    candidate: AttachmentCandidate,
    status: AttachmentDownloadStatus,
    *,
    data: bytes | None = None,
    storage_uri: str | None = None,
    fetch_attempts: int = 0,
    error_code: str | None = None,
    error_message: str | None = None,
) -> AttachmentRecord:
    actual_size = len(data) if data is not None else candidate.size_bytes
    digest = hashlib.sha256(data).hexdigest() if data is not None else None
    return AttachmentRecord(
        attachment_id=candidate.attachment_id,
        part_id=candidate.part_id,
        part_path=candidate.part_path,
        filename=candidate.filename,
        mime_type=candidate.mime_type,
        size_bytes=actual_size,
        content_hash_sha256=digest,
        storage_uri=storage_uri,
        download_status=status,
        downloaded_at=(
            datetime.now(UTC) if status is AttachmentDownloadStatus.DOWNLOADED else None
        ),
        is_inline=candidate.is_inline,
        content_id=candidate.content_id,
        fetch_attempts=fetch_attempts,
        error_code=error_code,
        error_message=error_message,
    )
