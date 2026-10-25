"""Resumable full snapshot of Gmail messages."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import local
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from pyingestkit.artifacts.base import ArtifactStore
from pyingestkit.retry import RetryPolicy

from gmail_ingestion.api import GmailApiClient, GmailApiError, map_gmail_profile
from gmail_ingestion.auth.provider import GmailOAuthProvider
from gmail_ingestion.control import MessageDiscoveryQueue
from gmail_ingestion.domain import MessageFetchStatus
from gmail_ingestion.manifest import GmailRunManifest, ManifestIssue, ManifestWriter
from gmail_ingestion.persistence import RawMessageWriter

from .message_processing import (
    AttachmentClientProtocol,
    MessagePostProcessor,
    MessageProcessingSummary,
)


class MessagesApiProtocol(Protocol):
    def list_messages(
        self,
        *,
        page_token: str | None = None,
        max_results: int = 500,
        include_spam_trash: bool = False,
        query: str | None = None,
        label_ids: Sequence[str] = (),
    ) -> Mapping[str, Any]: ...

    def get_message(self, message_id: str, *, format: str = "full") -> Mapping[str, Any]: ...


class GmailClientProtocol(AttachmentClientProtocol, Protocol):
    messages: MessagesApiProtocol

    def get_profile(self) -> Mapping[str, Any]: ...


ClientFactory = Callable[..., GmailApiClient]
SleepFn = Callable[[float], None]

_DEFAULT_QUEUE_CHECKPOINT_BATCH_SIZE = 1000
_MAX_IN_FLIGHT_MULTIPLIER = 2


@dataclass(frozen=True, slots=True)
class FullSnapshotResult:
    """Summary of one complete or resumable full-message snapshot run."""

    run_id: UUID
    account_id: str
    history_start: str | None
    pages_scanned: int
    discovered_count: int
    fetched_count: int
    failed_count: int
    raw_message_count: int
    mime_messages_processed: int
    mime_messages_failed: int
    mime_parts: int
    attachments_detected: int
    attachments_fetched: int
    attachments_failed: int
    attachments_skipped: int
    bytes_downloaded: int
    manifest_status: str
    manifest_path: str
    resumed: bool


class FullMessageSnapshot:
    """Execute GMAIL_03/GMAIL_04 snapshot, MIME, attachment and manifest flow."""

    def __init__(
        self,
        provider: GmailOAuthProvider,
        artifact_store: ArtifactStore,
        *,
        client_factory: ClientFactory = GmailApiClient,
        retry_sleep: SleepFn | None = None,
        queue_checkpoint_batch_size: int = _DEFAULT_QUEUE_CHECKPOINT_BATCH_SIZE,
    ) -> None:
        if queue_checkpoint_batch_size < 1:
            raise ValueError("queue_checkpoint_batch_size must be >= 1")
        self.provider = provider
        self.artifact_store = artifact_store
        self.client_factory = client_factory
        self.retry_sleep = retry_sleep
        self.queue_checkpoint_batch_size = queue_checkpoint_batch_size

    def run(
        self,
        account_id: str | None = None,
        *,
        run_id: UUID | None = None,
        include_spam_trash: bool = False,
        max_results: int = 500,
        max_workers: int = 4,
        max_attempts: int = 5,
        download_attachments: bool = False,
        max_attachment_bytes: int | None = None,
        message_limit: int | None = None,
    ) -> FullSnapshotResult:
        """Run or resume a full Gmail snapshot through MIME/attachment analysis."""
        if not 1 <= max_results <= 500:
            raise ValueError("max_results must be between 1 and 500")
        if not 1 <= max_workers <= 32:
            raise ValueError("max_workers must be between 1 and 32")
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if max_attachment_bytes is not None and max_attachment_bytes < 1:
            raise ValueError("max_attachment_bytes must be >= 1 when provided")
        if message_limit is not None and message_limit < 1:
            raise ValueError("message_limit must be >= 1 when provided")

        resolved, credentials = self.provider.load(account_id)
        source_client = cast(GmailClientProtocol, self.client_factory(credentials=credentials))
        profile = map_gmail_profile(resolved, source_client.get_profile())
        self.provider.store.verify_account_email(resolved, profile.email_address)

        actual_run_id = run_id or uuid4()
        manifest_writer = ManifestWriter(self.artifact_store)
        manifest_writer.validate_resume_settings(
            actual_run_id,
            include_spam_trash=include_spam_trash,
            download_attachments=download_attachments,
            max_attachment_bytes=max_attachment_bytes,
            message_limit=message_limit,
        )
        existing_started_at = manifest_writer.existing_started_at(actual_run_id)
        started_at = existing_started_at or datetime.now(UTC)
        queue: MessageDiscoveryQueue | None = None

        try:
            queue = MessageDiscoveryQueue.load_or_create(
                self.artifact_store,
                actual_run_id,
                resolved,
                profile.history_id,
            )
            resumed = queue.loaded_existing or existing_started_at is not None
            manifest_writer.write(
                self._manifest(
                    run_id=actual_run_id,
                    account_id=resolved,
                    started_at=started_at,
                    completed_at=None,
                    status="RUNNING",
                    queue=queue,
                    include_spam_trash=include_spam_trash,
                    download_attachments=download_attachments,
                    max_attachment_bytes=max_attachment_bytes,
                    message_limit=message_limit,
                    resumed=resumed,
                )
            )

            if not queue.discovery_complete:
                self._discover(
                    source_client,
                    queue,
                    include_spam_trash=include_spam_trash,
                    max_results=max_results,
                    message_limit=message_limit,
                )

            writer = RawMessageWriter(self.artifact_store)
            recovered_existing = 0
            for record in queue.pending():
                if writer.exists(actual_run_id, record.message_id):
                    queue.mark_fetched(record.message_id, attempts=0)
                    recovered_existing += 1
            if recovered_existing:
                queue.persist()

            retry = self._retry_policy(max_attempts)
            workers = _WorkerClientPool(self.client_factory, credentials)
            self._hydrate_pending(
                queue,
                writer,
                workers,
                retry,
                max_workers=max_workers,
            )

            processing = MessagePostProcessor(
                self.artifact_store,
                source_client,
                retry,
                download_attachments=download_attachments,
                max_attachment_bytes=max_attachment_bytes,
            ).process(actual_run_id, resolved, queue.records)

            message_errors = _message_fetch_issues(queue)
            errors = message_errors + processing.errors
            status = (
                "PARTIAL"
                if queue.failed_count
                or processing.mime_messages_failed
                or processing.attachments_failed
                else "COMPLETED"
            )
            completed_at = datetime.now(UTC)
            final_manifest = self._manifest(
                run_id=actual_run_id,
                account_id=resolved,
                started_at=started_at,
                completed_at=completed_at,
                status=status,
                queue=queue,
                include_spam_trash=include_spam_trash,
                download_attachments=download_attachments,
                max_attachment_bytes=max_attachment_bytes,
                message_limit=message_limit,
                resumed=resumed,
                processing=processing,
                errors=errors,
                warnings=processing.warnings,
            )
            manifest_path = manifest_writer.write(final_manifest)

            return FullSnapshotResult(
                run_id=actual_run_id,
                account_id=resolved,
                history_start=queue.history_start,
                pages_scanned=queue.pages_scanned,
                discovered_count=len(queue.records),
                fetched_count=queue.fetched_count,
                failed_count=queue.failed_count,
                raw_message_count=queue.fetched_count,
                mime_messages_processed=processing.mime_messages_processed,
                mime_messages_failed=processing.mime_messages_failed,
                mime_parts=processing.mime_parts,
                attachments_detected=processing.attachments_detected,
                attachments_fetched=processing.attachments_fetched,
                attachments_failed=processing.attachments_failed,
                attachments_skipped=processing.attachments_skipped,
                bytes_downloaded=processing.bytes_downloaded,
                manifest_status=status,
                manifest_path=manifest_path,
                resumed=resumed,
            )
        except Exception as exc:
            if queue is not None:
                issue = ManifestIssue(
                    scope="run",
                    code="SNAPSHOT_FATAL_ERROR",
                    message=str(exc),
                )
                try:
                    manifest_writer.write(
                        self._manifest(
                            run_id=actual_run_id,
                            account_id=resolved,
                            started_at=started_at,
                            completed_at=datetime.now(UTC),
                            status="FAILED",
                            queue=queue,
                            include_spam_trash=include_spam_trash,
                            download_attachments=download_attachments,
                            max_attachment_bytes=max_attachment_bytes,
                            message_limit=message_limit,
                            resumed=queue.loaded_existing or existing_started_at is not None,
                            errors=(issue,),
                        )
                    )
                except Exception:  # noqa: BLE001 - preserve the original fatal exception
                    pass
            raise

    def _hydrate_pending(
        self,
        queue: MessageDiscoveryQueue,
        writer: RawMessageWriter,
        workers: _WorkerClientPool,
        retry: RetryPolicy,
        *,
        max_workers: int,
    ) -> None:
        pending = iter(queue.iter_pending())
        in_flight: dict[Future[tuple[Mapping[str, Any], int]], str] = {}
        dirty = 0
        max_in_flight = max_workers * _MAX_IN_FLIGHT_MULTIPLIER

        def submit_next(executor: ThreadPoolExecutor) -> bool:
            try:
                record = next(pending)
            except StopIteration:
                return False
            future = executor.submit(
                _fetch_with_retry,
                workers,
                record.message_id,
                retry,
            )
            in_flight[future] = record.message_id
            return True

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for _ in range(max_in_flight):
                if not submit_next(executor):
                    break

            try:
                while in_flight:
                    done, _ = wait(tuple(in_flight), return_when=FIRST_COMPLETED)
                    for future in done:
                        message_id = in_flight.pop(future)
                        try:
                            payload, attempts = future.result()
                        except MessageFetchFailure as exc:
                            queue.mark_failed(
                                message_id,
                                attempts=exc.attempts,
                                error_code="GMAIL_API_ERROR",
                                error_message=str(exc),
                            )
                        else:
                            writer.write(queue.run_id, message_id, payload)
                            queue.mark_fetched(message_id, attempts=attempts)

                        dirty += 1
                        if dirty >= self.queue_checkpoint_batch_size:
                            queue.persist()
                            dirty = 0
                        submit_next(executor)
            except KeyboardInterrupt:
                if dirty:
                    queue.persist()
                for future in in_flight:
                    future.cancel()
                raise

        if dirty:
            queue.persist()

    def _discover(
        self,
        client: GmailClientProtocol,
        queue: MessageDiscoveryQueue,
        *,
        include_spam_trash: bool,
        max_results: int,
        message_limit: int | None,
    ) -> None:
        if message_limit is not None and len(queue.records) >= message_limit:
            queue.mark_discovery_complete()
            queue.persist()
            return

        page_token = queue.next_page_token
        page_index = queue.pages_scanned if page_token is not None else 0

        while True:
            page = client.messages.list_messages(
                page_token=page_token,
                max_results=max_results,
                include_spam_trash=include_spam_trash,
            )
            refs = _message_refs(page)
            if message_limit is not None:
                remaining = message_limit - len(queue.records)
                refs = refs[:remaining]
            queue.merge_page(refs, page_index=page_index)

            if message_limit is not None and len(queue.records) >= message_limit:
                queue.mark_discovery_complete()
                queue.persist()
                return

            next_page_token = _next_page_token(page)
            queue.checkpoint_discovery_page(next_page_token)
            queue.persist()
            if next_page_token is None:
                return

            page_token = next_page_token
            page_index += 1

    def _retry_policy(self, max_attempts: int) -> RetryPolicy:
        kwargs: dict[str, Any] = {
            "max_attempts": max_attempts,
            "initial_delay_seconds": 1.0,
            "max_delay_seconds": 30.0,
            "jitter": True,
        }
        if self.retry_sleep is not None:
            kwargs["sleep"] = self.retry_sleep
        return RetryPolicy(**kwargs)

    def _manifest(
        self,
        *,
        run_id: UUID,
        account_id: str,
        started_at: datetime,
        completed_at: datetime | None,
        status: str,
        queue: MessageDiscoveryQueue,
        include_spam_trash: bool,
        download_attachments: bool,
        max_attachment_bytes: int | None,
        message_limit: int | None,
        resumed: bool,
        processing: MessageProcessingSummary | None = None,
        errors: tuple[ManifestIssue, ...] = (),
        warnings: tuple[ManifestIssue, ...] = (),
    ) -> GmailRunManifest:
        return GmailRunManifest(
            run_id=run_id,
            account_id=account_id,
            started_at=started_at,
            completed_at=completed_at,
            status=status,
            history_start=queue.history_start,
            include_spam_trash=include_spam_trash,
            download_attachments=download_attachments,
            max_attachment_bytes=max_attachment_bytes,
            messages_discovered=len(queue.records),
            messages_fetched=queue.fetched_count,
            messages_failed=queue.failed_count,
            message_limit=message_limit,
            mime_parts=processing.mime_parts if processing is not None else 0,
            mime_messages_processed=(
                processing.mime_messages_processed if processing is not None else 0
            ),
            mime_messages_failed=(
                processing.mime_messages_failed if processing is not None else 0
            ),
            attachments_detected=(
                processing.attachments_detected if processing is not None else 0
            ),
            attachments_fetched=(
                processing.attachments_fetched if processing is not None else 0
            ),
            attachments_failed=(
                processing.attachments_failed if processing is not None else 0
            ),
            attachments_skipped=(
                processing.attachments_skipped if processing is not None else 0
            ),
            bytes_downloaded=processing.bytes_downloaded if processing is not None else 0,
            resumed=resumed,
            errors=errors,
            warnings=warnings,
            storage_paths=_storage_paths(self.artifact_store, run_id),
        )


class MessageFetchFailure(RuntimeError):
    """Final safe failure after bounded retries for one message."""

    def __init__(self, message_id: str, attempts: int, cause: GmailApiError) -> None:
        super().__init__(f"Unable to fetch Gmail message {message_id!r} after {attempts} attempts")
        self.attempts = attempts
        self.__cause__ = cause


class _WorkerClientPool:
    """Create one Google API service per worker thread, not per message."""

    def __init__(self, factory: ClientFactory, credentials: Any) -> None:
        self.factory = factory
        self.credentials = credentials
        self._local = local()

    def get(self) -> GmailClientProtocol:
        existing = getattr(self._local, "client", None)
        if existing is None:
            existing = self.factory(credentials=self.credentials)
            self._local.client = existing
        return cast(GmailClientProtocol, existing)


def _fetch_with_retry(
    workers: _WorkerClientPool,
    message_id: str,
    retry: RetryPolicy,
) -> tuple[Mapping[str, Any], int]:
    attempts = 0

    def operation() -> Mapping[str, Any]:
        nonlocal attempts
        attempts += 1
        return workers.get().messages.get_message(message_id, format="full")

    try:
        payload = retry.execute(
            operation,
            should_retry=lambda exc: isinstance(exc, GmailApiError),
        )
    except GmailApiError as exc:
        raise MessageFetchFailure(message_id, attempts, exc) from exc
    return payload, attempts


def _message_refs(page: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw = page.get("messages", [])
    if not isinstance(raw, list):
        raise GmailApiError("Gmail messages.list response contains an invalid messages field")
    result: list[Mapping[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise GmailApiError("Gmail messages.list contains a non-object message reference")
        result.append(item)
    return result


def _next_page_token(page: Mapping[str, Any]) -> str | None:
    value = page.get("nextPageToken")
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise GmailApiError("Gmail messages.list returned an invalid nextPageToken")
    return value


def _message_fetch_issues(queue: MessageDiscoveryQueue) -> tuple[ManifestIssue, ...]:
    issues: list[ManifestIssue] = []
    for record in queue.records.values():
        if record.fetch_status is not MessageFetchStatus.FAILED:
            continue
        issues.append(
            ManifestIssue(
                scope="message",
                code=record.error_code or "MESSAGE_FETCH_ERROR",
                message=record.error_message or "Gmail message fetch failed",
                message_id=record.message_id,
            )
        )
    return tuple(issues)


def _storage_paths(store: ArtifactStore, run_id: UUID) -> dict[str, str]:
    return {
        "manifest": str(store.uri_for("gmail.messages", run_id, "manifest.json")),
        "message_discovery": str(
            store.uri_for("gmail.messages", run_id, "control/message_discovery.json")
        ),
        "raw_messages": str(store.uri_for("gmail.messages", run_id, "raw")),
        "mime_analysis": str(store.uri_for("gmail.messages", run_id, "candidate/mime")),
        "attachment_binaries": str(store.uri_for("gmail.attachments", run_id, "raw")),
    }
