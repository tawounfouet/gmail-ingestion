"""Resumable full snapshot of Gmail messages."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from threading import local
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from pyingestkit.artifacts.base import ArtifactStore
from pyingestkit.retry import RetryPolicy

from gmail_ingestion.api import GmailApiClient, GmailApiError, map_gmail_profile
from gmail_ingestion.auth.provider import GmailOAuthProvider
from gmail_ingestion.control import MessageDiscoveryQueue
from gmail_ingestion.persistence import RawMessageWriter


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


class GmailClientProtocol(Protocol):
    messages: MessagesApiProtocol

    def get_profile(self) -> Mapping[str, Any]: ...


ClientFactory = Callable[..., GmailApiClient]
SleepFn = Callable[[float], None]


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
    resumed: bool


class FullMessageSnapshot:
    """Execute GMAIL_03 discovery, hydration, RAW persistence and resume."""

    def __init__(
        self,
        provider: GmailOAuthProvider,
        artifact_store: ArtifactStore,
        *,
        client_factory: ClientFactory = GmailApiClient,
        retry_sleep: SleepFn | None = None,
    ) -> None:
        self.provider = provider
        self.artifact_store = artifact_store
        self.client_factory = client_factory
        self.retry_sleep = retry_sleep

    def run(
        self,
        account_id: str | None = None,
        *,
        run_id: UUID | None = None,
        include_spam_trash: bool = False,
        max_results: int = 500,
        max_workers: int = 4,
        max_attempts: int = 5,
    ) -> FullSnapshotResult:
        """Run or resume a full Gmail message snapshot."""
        if not 1 <= max_results <= 500:
            raise ValueError("max_results must be between 1 and 500")
        if not 1 <= max_workers <= 32:
            raise ValueError("max_workers must be between 1 and 32")
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")

        resolved, credentials = self.provider.load(account_id)
        source_client = cast(GmailClientProtocol, self.client_factory(credentials=credentials))
        profile = map_gmail_profile(resolved, source_client.get_profile())
        self.provider.store.verify_account_email(resolved, profile.email_address)

        actual_run_id = run_id or uuid4()
        queue = MessageDiscoveryQueue.load_or_create(
            self.artifact_store,
            actual_run_id,
            resolved,
            profile.history_id,
        )

        if not queue.discovery_complete:
            self._discover(
                source_client,
                queue,
                include_spam_trash=include_spam_trash,
                max_results=max_results,
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
        pending = queue.pending()
        if pending:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures: dict[Future[tuple[Mapping[str, Any], int]], str] = {
                    executor.submit(
                        _fetch_with_retry,
                        workers,
                        record.message_id,
                        retry,
                    ): record.message_id
                    for record in pending
                }
                for future in as_completed(futures):
                    message_id = futures[future]
                    try:
                        payload, attempts = future.result()
                    except MessageFetchFailure as exc:
                        queue.mark_failed(
                            message_id,
                            attempts=exc.attempts,
                            error_code="GMAIL_API_ERROR",
                            error_message=str(exc),
                        )
                        queue.persist()
                        continue

                    writer.write(actual_run_id, message_id, payload)
                    queue.mark_fetched(message_id, attempts=attempts)
                    queue.persist()

        return FullSnapshotResult(
            run_id=actual_run_id,
            account_id=resolved,
            history_start=queue.history_start,
            pages_scanned=queue.pages_scanned,
            discovered_count=len(queue.records),
            fetched_count=queue.fetched_count,
            failed_count=queue.failed_count,
            raw_message_count=queue.fetched_count,
            resumed=queue.loaded_existing,
        )

    def _discover(
        self,
        client: GmailClientProtocol,
        queue: MessageDiscoveryQueue,
        *,
        include_spam_trash: bool,
        max_results: int,
    ) -> None:
        page_token: str | None = None
        page_index = 0

        while True:
            page = client.messages.list_messages(
                page_token=page_token,
                max_results=max_results,
                include_spam_trash=include_spam_trash,
            )
            queue.merge_page(_message_refs(page), page_index=page_index)
            queue.persist()

            page_token = _next_page_token(page)
            if page_token is None:
                queue.mark_discovery_complete()
                queue.persist()
                return
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
