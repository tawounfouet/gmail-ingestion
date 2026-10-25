"""Date-range Gmail snapshot use case."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from uuid import UUID

from pyingestkit.artifacts.base import ArtifactStore

from gmail_ingestion.api import GmailApiClient
from gmail_ingestion.auth.provider import GmailOAuthProvider
from gmail_ingestion.domain import DateRange

from .full_sync import FullMessageSnapshot, FullSnapshotResult

ClientFactory = Callable[..., GmailApiClient]
SleepFn = Callable[[float], None]


class RangeMessageSnapshot:
    """Execute an explicit absolute or relative Gmail date-range snapshot."""

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
        date_range: DateRange,
        account_id: str | None = None,
        *,
        run_id: UUID | None = None,
        query: str | None = None,
        label_ids: Sequence[str] = (),
        include_spam_trash: bool = False,
        max_results: int = 500,
        max_workers: int = 4,
        max_attempts: int = 5,
        download_attachments: bool = False,
        max_attachment_bytes: int | None = None,
        message_limit: int | None = None,
    ) -> FullSnapshotResult:
        """Run a date-bounded snapshot through the shared snapshot engine."""
        return self._snapshot.run(
            account_id,
            run_id=run_id,
            query=query,
            label_ids=label_ids,
            include_spam_trash=include_spam_trash,
            after=date_range.after,
            before=date_range.before,
            last_days=date_range.last_days,
            max_results=max_results,
            max_workers=max_workers,
            max_attempts=max_attempts,
            download_attachments=download_attachments,
            max_attachment_bytes=max_attachment_bytes,
            message_limit=message_limit,
        )
