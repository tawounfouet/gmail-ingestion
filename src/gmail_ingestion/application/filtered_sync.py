"""Filtered Gmail snapshot use case."""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from pyingestkit.artifacts.base import ArtifactStore

from gmail_ingestion.api import GmailApiClient
from gmail_ingestion.auth.provider import GmailOAuthProvider
from gmail_ingestion.domain import GmailFilter

from .full_sync import FullMessageSnapshot, FullSnapshotResult

ClientFactory = Callable[..., GmailApiClient]
SleepFn = Callable[[float], None]


class FilteredMessageSnapshot:
    """Execute a Gmail snapshot constrained by an explicit search/label scope."""

    def __init__(
        self,
        provider: GmailOAuthProvider,
        artifact_store: ArtifactStore,
        *,
        client_factory: ClientFactory = GmailApiClient,
        retry_sleep: SleepFn | None = None,
        queue_checkpoint_batch_size: int = 1000,
    ) -> None:
        self._snapshot = FullMessageSnapshot(
            provider,
            artifact_store,
            client_factory=client_factory,
            retry_sleep=retry_sleep,
            queue_checkpoint_batch_size=queue_checkpoint_batch_size,
        )

    def run(
        self,
        gmail_filter: GmailFilter,
        account_id: str | None = None,
        *,
        run_id: UUID | None = None,
        max_results: int = 500,
        max_workers: int = 4,
        max_attempts: int = 5,
        download_attachments: bool = False,
        max_attachment_bytes: int | None = None,
        message_limit: int | None = None,
    ) -> FullSnapshotResult:
        """Run a filtered snapshot while reusing the stable full-snapshot pipeline."""
        if not gmail_filter.is_filtered:
            raise ValueError(
                "FilteredMessageSnapshot requires query and/or label_ids; "
                "use FullMessageSnapshot for an unfiltered scope"
            )

        date_range = gmail_filter.date_range
        return self._snapshot.run(
            account_id,
            run_id=run_id,
            include_spam_trash=gmail_filter.include_spam_trash,
            max_results=max_results,
            max_workers=max_workers,
            max_attempts=max_attempts,
            download_attachments=download_attachments,
            max_attachment_bytes=max_attachment_bytes,
            message_limit=message_limit,
            query=gmail_filter.query,
            label_ids=gmail_filter.canonical_label_ids,
            after=date_range.requested_after if date_range is not None else None,
            before=date_range.requested_before if date_range is not None else None,
            last_days=date_range.requested_last_days if date_range is not None else None,
            resolution_timestamp=(
                date_range.resolution_timestamp if date_range is not None else None
            ),
        )
