"""Stateful Gmail History incremental synchronization for 0.4.0b1."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from pyingestkit.artifacts.base import ArtifactStore

from gmail_ingestion.api import GmailApiClient, GmailHistoryExpiredError
from gmail_ingestion.auth.provider import GmailOAuthProvider
from gmail_ingestion.domain import GmailCheckpoint, map_message_added_events
from gmail_ingestion.persistence import (
    GmailCheckpointStore,
    HistoryEventWriter,
    IncrementalManifestWriter,
    RawHistoryWriter,
    RawMessageWriter,
)


class MissingCommittedCheckpointError(RuntimeError):
    """Raised when incremental sync is requested before a durable cursor exists."""


class FullResyncRequiredError(RuntimeError):
    """Raised when Gmail rejects the committed history cursor as expired or invalid."""


class HistoryApiProtocol(Protocol):
    def list_history(
        self,
        *,
        start_history_id: str,
        page_token: str | None = None,
        max_results: int = 500,
        history_types: Sequence[str] = (),
        label_id: str | None = None,
    ) -> Mapping[str, Any]: ...


class MessagesApiProtocol(Protocol):
    def get_message(self, message_id: str, *, format: str = "full") -> Mapping[str, Any]: ...


class IncrementalClientProtocol(Protocol):
    history: HistoryApiProtocol
    messages: MessagesApiProtocol


ClientFactory = Callable[..., GmailApiClient]


@dataclass(frozen=True, slots=True)
class IncrementalHistoryResult:
    """Summary of one committed Gmail History incremental run."""

    run_id: UUID
    account_id: str
    input_history_id: str
    output_history_id: str
    pages_scanned: int
    events_persisted: int
    messages_hydrated: int
    committed_checkpoint: GmailCheckpoint
    manifest_path: str


class IncrementalHistorySync:
    """Apply messageAdded changes after the current committed Gmail checkpoint."""

    def __init__(
        self,
        provider: GmailOAuthProvider,
        artifact_store: ArtifactStore,
        checkpoint_store: GmailCheckpointStore,
        *,
        client_factory: ClientFactory = GmailApiClient,
    ) -> None:
        self.provider = provider
        self.artifact_store = artifact_store
        self.checkpoint_store = checkpoint_store
        self.client_factory = client_factory

    def run(
        self,
        account_id: str | None = None,
        *,
        run_id: UUID | None = None,
        max_results: int = 500,
    ) -> IncrementalHistoryResult:
        if not 1 <= max_results <= 500:
            raise ValueError("max_results must be between 1 and 500")

        actual_run_id = run_id or uuid4()
        resolved, credentials = self.provider.load(account_id)
        input_checkpoint = self.checkpoint_store.load_committed(resolved)
        if input_checkpoint is None:
            raise MissingCommittedCheckpointError(
                f"No committed Gmail checkpoint exists for {resolved}; "
                "establish one from a qualified snapshot before incremental sync"
            )

        client = cast(
            IncrementalClientProtocol,
            self.client_factory(credentials=credentials),
        )
        history_writer = RawHistoryWriter(self.artifact_store)
        event_writer = HistoryEventWriter(self.artifact_store)
        message_writer = RawMessageWriter(self.artifact_store)
        manifest_writer = IncrementalManifestWriter(self.artifact_store)

        started_at = datetime.now(UTC)
        input_history_id = input_checkpoint.history_id
        output_history_id = input_history_id
        page_token: str | None = None
        page_index = 0
        event_count = 0
        hydrated_message_ids: set[str] = set()

        try:
            while True:
                try:
                    page = client.history.list_history(
                        start_history_id=input_history_id,
                        page_token=page_token,
                        max_results=max_results,
                        history_types=("messageAdded",),
                    )
                except GmailHistoryExpiredError as exc:
                    self.checkpoint_store.invalidate(
                        resolved,
                        "Gmail rejected startHistoryId as invalid or expired",
                    )
                    _best_effort_manifest(
                        manifest_writer,
                        actual_run_id,
                        _manifest_payload(
                            run_id=actual_run_id,
                            account_id=resolved,
                            started_at=started_at,
                            completed_at=datetime.now(UTC),
                            status="FULL_RESYNC_REQUIRED",
                            input_checkpoint=input_checkpoint,
                            output_history_id=None,
                            pages_scanned=page_index,
                            events_persisted=event_count,
                            messages_hydrated=len(hydrated_message_ids),
                        ),
                    )
                    raise FullResyncRequiredError(
                        "Committed Gmail history checkpoint is invalid or expired; "
                        "run a full sync to establish a new checkpoint"
                    ) from exc

                observed_at = datetime.now(UTC)
                history_writer.write_page(
                    actual_run_id,
                    page_index,
                    page,
                    start_history_id=input_history_id,
                    page_token=page_token,
                )
                mapped = map_message_added_events(
                    account_id=resolved,
                    run_id=actual_run_id,
                    page=page,
                    observed_at=observed_at,
                    source_page_token=page_token,
                )
                for event in mapped:
                    event_writer.write(event)
                    event_count += 1

                for event in mapped:
                    if event.message_id in hydrated_message_ids:
                        continue
                    payload = client.messages.get_message(event.message_id, format="full")
                    message_writer.write(actual_run_id, event.message_id, payload)
                    hydrated_message_ids.add(event.message_id)

                page_history_id = page.get("historyId")
                if isinstance(page_history_id, str) and page_history_id:
                    output_history_id = page_history_id

                raw_next_page_token = page.get("nextPageToken")
                page_index += 1
                if raw_next_page_token is None:
                    break
                if not isinstance(raw_next_page_token, str) or not raw_next_page_token:
                    raise ValueError("Gmail history response contains an invalid nextPageToken")
                page_token = raw_next_page_token

            candidate = GmailCheckpoint.candidate(
                account_id=resolved,
                history_id=output_history_id,
                run_id=actual_run_id,
                dataset_version=str(actual_run_id),
            )
            self.checkpoint_store.save_candidate(candidate)
            committed = self.checkpoint_store.commit(candidate)
            completed_at = datetime.now(UTC)
            manifest_path = manifest_writer.write(
                actual_run_id,
                _manifest_payload(
                    run_id=actual_run_id,
                    account_id=resolved,
                    started_at=started_at,
                    completed_at=completed_at,
                    status="COMPLETED",
                    input_checkpoint=input_checkpoint,
                    output_history_id=committed.history_id,
                    pages_scanned=page_index,
                    events_persisted=event_count,
                    messages_hydrated=len(hydrated_message_ids),
                    committed_checkpoint=committed,
                ),
            )
            return IncrementalHistoryResult(
                run_id=actual_run_id,
                account_id=resolved,
                input_history_id=input_history_id,
                output_history_id=committed.history_id,
                pages_scanned=page_index,
                events_persisted=event_count,
                messages_hydrated=len(hydrated_message_ids),
                committed_checkpoint=committed,
                manifest_path=manifest_path,
            )
        except FullResyncRequiredError:
            raise
        except Exception:
            _best_effort_manifest(
                manifest_writer,
                actual_run_id,
                _manifest_payload(
                    run_id=actual_run_id,
                    account_id=resolved,
                    started_at=started_at,
                    completed_at=datetime.now(UTC),
                    status="FAILED",
                    input_checkpoint=input_checkpoint,
                    output_history_id=None,
                    pages_scanned=page_index,
                    events_persisted=event_count,
                    messages_hydrated=len(hydrated_message_ids),
                ),
            )
            raise


def _manifest_payload(
    *,
    run_id: UUID,
    account_id: str,
    started_at: datetime,
    completed_at: datetime,
    status: str,
    input_checkpoint: GmailCheckpoint,
    output_history_id: str | None,
    pages_scanned: int,
    events_persisted: int,
    messages_hydrated: int,
    committed_checkpoint: GmailCheckpoint | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "incremental_history",
        "history_types": ["messageAdded"],
        "run_id": str(run_id),
        "account_id": account_id,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "status": status,
        "input_checkpoint": input_checkpoint.history_id,
        "candidate_checkpoint": output_history_id,
        "committed_checkpoint": (
            committed_checkpoint.history_id if committed_checkpoint is not None else None
        ),
        "pages_scanned": pages_scanned,
        "events_persisted": events_persisted,
        "messages_hydrated": messages_hydrated,
        "message_format": "full",
    }


def _best_effort_manifest(
    writer: IncrementalManifestWriter,
    run_id: UUID,
    payload: Mapping[str, Any],
) -> None:
    try:
        writer.write(run_id, payload)
    except Exception:  # noqa: BLE001 - never hide the original incremental failure
        pass
