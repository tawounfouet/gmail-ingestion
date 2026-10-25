"""Crash-safe Gmail History incremental synchronization for 0.4.0rc1."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from pyingestkit.artifacts.base import ArtifactStore

from gmail_ingestion.api import GmailApiClient, GmailHistoryExpiredError
from gmail_ingestion.auth.provider import GmailOAuthProvider
from gmail_ingestion.domain import (
    GmailCheckpoint,
    HistoryProcessingStatus,
    map_history_events,
)
from gmail_ingestion.persistence import (
    CheckpointConflictError,
    GmailCheckpointStore,
    GmailHistoryReceiptStore,
    HistoryEventWriter,
    IncrementalManifestWriter,
    IncrementalMutationWriter,
    RawHistoryWriter,
    RawMessageWriter,
)
from gmail_ingestion.sync import reduce_history_event

_SUPPORTED_HISTORY_TYPES = (
    "messageAdded",
    "messageDeleted",
    "labelAdded",
    "labelRemoved",
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
    """Summary of one committed or recovered Gmail History run."""

    run_id: UUID
    account_id: str
    input_history_id: str
    output_history_id: str
    pages_scanned: int
    reused_pages: int
    events_persisted: int
    duplicate_events_ignored: int
    replayed_events: int
    messages_hydrated: int
    messages_reused: int
    message_tombstones: int
    label_mutations: int
    committed_checkpoint: GmailCheckpoint
    manifest_path: str
    recovered: bool = False


class IncrementalHistorySync:
    """Apply Gmail History transactionally with deterministic replay and CAS promotion."""

    def __init__(
        self,
        provider: GmailOAuthProvider,
        artifact_store: ArtifactStore,
        checkpoint_store: GmailCheckpointStore,
        *,
        receipt_store: GmailHistoryReceiptStore | None = None,
        client_factory: ClientFactory = GmailApiClient,
    ) -> None:
        self.provider = provider
        self.artifact_store = artifact_store
        self.checkpoint_store = checkpoint_store
        self.receipt_store = receipt_store
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

        history_writer = RawHistoryWriter(self.artifact_store)
        event_writer = HistoryEventWriter(self.artifact_store)
        mutation_writer = IncrementalMutationWriter(self.artifact_store)
        message_writer = RawMessageWriter(self.artifact_store)
        manifest_writer = IncrementalManifestWriter(self.artifact_store)

        previous_manifest = manifest_writer.read(actual_run_id)
        started_at = _manifest_started_at(previous_manifest) or datetime.now(UTC)
        input_checkpoint = self.checkpoint_store.load_committed(resolved)

        if input_checkpoint is not None and input_checkpoint.run_id == actual_run_id:
            return _already_committed_result(
                manifest_writer=manifest_writer,
                run_id=actual_run_id,
                account_id=resolved,
                committed=input_checkpoint,
                previous_manifest=previous_manifest,
                started_at=started_at,
            )

        candidate = self.checkpoint_store.load_candidate(resolved, actual_run_id)
        if candidate is not None:
            if input_checkpoint is None:
                raise CheckpointConflictError(
                    "Cannot recover candidate without its predecessor committed checkpoint"
                )
            expected_input = _manifest_string(previous_manifest, "input_checkpoint")
            if expected_input is not None and expected_input != input_checkpoint.history_id:
                raise CheckpointConflictError(
                    "Candidate predecessor changed before recovery: "
                    f"expected {expected_input!r}, got {input_checkpoint.history_id!r}"
                )
            committed = self.checkpoint_store.commit(
                candidate,
                expected_committed_history_id=input_checkpoint.history_id,
            )
            return _recovered_candidate_result(
                manifest_writer=manifest_writer,
                run_id=actual_run_id,
                account_id=resolved,
                input_checkpoint=input_checkpoint,
                committed=committed,
                previous_manifest=previous_manifest,
                started_at=started_at,
            )

        if input_checkpoint is None:
            raise MissingCommittedCheckpointError(
                f"No committed Gmail checkpoint exists for {resolved}; "
                "establish one from a qualified snapshot before incremental sync"
            )

        input_history_id = input_checkpoint.history_id
        previous_input = _manifest_string(previous_manifest, "input_checkpoint")
        if (
            previous_input is not None
            and previous_input != input_history_id
            and history_writer.read_page(actual_run_id, 0) is not None
        ):
            raise CheckpointConflictError(
                "Cannot replay persisted History pages against a different committed checkpoint"
            )

        manifest_writer.write(
            actual_run_id,
            _manifest_payload(
                run_id=actual_run_id,
                account_id=resolved,
                started_at=started_at,
                completed_at=None,
                status="RUNNING",
                input_checkpoint=input_checkpoint,
                output_history_id=None,
                pages_scanned=0,
                reused_pages=0,
                events_persisted=0,
                duplicate_events_ignored=0,
                replayed_events=0,
                messages_hydrated=0,
                messages_reused=0,
                message_tombstones=0,
                label_mutations=0,
                durable_receipts=self.receipt_store is not None,
            ),
        )

        client = cast(
            IncrementalClientProtocol,
            self.client_factory(credentials=credentials),
        )

        output_history_id = input_history_id
        page_token: str | None = None
        page_index = 0
        reused_page_count = 0
        event_count = 0
        duplicate_event_count = 0
        replayed_event_count = 0
        tombstone_count = 0
        label_mutation_count = 0
        hydrated_message_ids: set[str] = set()
        reused_message_ids: set[str] = set()
        seen_event_ids: set[str] = set()
        candidate_history_id: str | None = None

        try:
            while True:
                page = history_writer.read_page(actual_run_id, page_index)
                if page is not None:
                    reused_page_count += 1
                else:
                    try:
                        page = client.history.list_history(
                            start_history_id=input_history_id,
                            page_token=page_token,
                            max_results=max_results,
                            history_types=_SUPPORTED_HISTORY_TYPES,
                        )
                    except GmailHistoryExpiredError as exc:
                        self.checkpoint_store.invalidate(
                            resolved,
                            "Gmail rejected startHistoryId as invalid or expired",
                            expected_history_id=input_history_id,
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
                                reused_pages=reused_page_count,
                                events_persisted=event_count,
                                duplicate_events_ignored=duplicate_event_count,
                                replayed_events=replayed_event_count,
                                messages_hydrated=len(hydrated_message_ids),
                                messages_reused=len(reused_message_ids),
                                message_tombstones=tombstone_count,
                                label_mutations=label_mutation_count,
                                durable_receipts=self.receipt_store is not None,
                            ),
                        )
                        raise FullResyncRequiredError(
                            "Committed Gmail history checkpoint is invalid or expired; "
                            "a full sync snapshot is required"
                        ) from exc

                    history_writer.write_page(
                        actual_run_id,
                        page_index,
                        page,
                        start_history_id=input_history_id,
                        page_token=page_token,
                    )

                observed_at = datetime.now(UTC)
                mapped = map_history_events(
                    account_id=resolved,
                    run_id=actual_run_id,
                    page=page,
                    observed_at=observed_at,
                    source_page_token=page_token,
                )
                for mapped_event in mapped:
                    if mapped_event.event_id in seen_event_ids:
                        duplicate_event_count += 1
                        continue
                    seen_event_ids.add(mapped_event.event_id)
                    event_count += 1

                    persisted_event = event_writer.read(
                        actual_run_id,
                        mapped_event.event_id,
                    )
                    event = persisted_event or mapped_event

                    if (
                        persisted_event is not None
                        and persisted_event.processing_status
                        is HistoryProcessingStatus.PROCESSED
                    ):
                        if self.receipt_store is not None:
                            self.receipt_store.mark_applied(
                                persisted_event,
                                applied_at=persisted_event.processed_at,
                            )
                        replayed_event_count += 1
                        continue

                    receipt = (
                        self.receipt_store.load(resolved, event.event_id)
                        if self.receipt_store is not None
                        else None
                    )
                    if receipt is not None:
                        if persisted_event is None:
                            event_writer.write(event)
                        event_writer.write(
                            event.mark_processed(processed_at=receipt.applied_at)
                        )
                        replayed_event_count += 1
                        continue

                    if persisted_event is None:
                        event_writer.write(event)

                    reduction = reduce_history_event(event)

                    if reduction.hydrate_message_id is not None:
                        message_id = reduction.hydrate_message_id
                        if (
                            message_id not in hydrated_message_ids
                            and message_id not in reused_message_ids
                        ):
                            if message_writer.exists(actual_run_id, message_id):
                                reused_message_ids.add(message_id)
                            else:
                                payload = client.messages.get_message(
                                    message_id,
                                    format="full",
                                )
                                message_writer.write(actual_run_id, message_id, payload)
                                hydrated_message_ids.add(message_id)

                    if reduction.tombstone is not None:
                        mutation_writer.write_tombstone(reduction.tombstone)
                        tombstone_count += 1

                    if reduction.label_mutation is not None:
                        mutation_writer.write_label_mutation(reduction.label_mutation)
                        label_mutation_count += 1

                    processed_at = datetime.now(UTC)
                    if self.receipt_store is not None:
                        receipt = self.receipt_store.mark_applied(
                            event,
                            applied_at=processed_at,
                        )
                        processed_at = receipt.applied_at
                    event_writer.write(event.mark_processed(processed_at=processed_at))

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
            candidate_history_id = candidate.history_id

            manifest_writer.write(
                actual_run_id,
                _manifest_payload(
                    run_id=actual_run_id,
                    account_id=resolved,
                    started_at=started_at,
                    completed_at=None,
                    status="READY_TO_COMMIT",
                    input_checkpoint=input_checkpoint,
                    output_history_id=candidate.history_id,
                    pages_scanned=page_index,
                    reused_pages=reused_page_count,
                    events_persisted=event_count,
                    duplicate_events_ignored=duplicate_event_count,
                    replayed_events=replayed_event_count,
                    messages_hydrated=len(hydrated_message_ids),
                    messages_reused=len(reused_message_ids),
                    message_tombstones=tombstone_count,
                    label_mutations=label_mutation_count,
                    durable_receipts=self.receipt_store is not None,
                ),
            )

            committed = self.checkpoint_store.commit(
                candidate,
                expected_committed_history_id=input_history_id,
            )
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
                    reused_pages=reused_page_count,
                    events_persisted=event_count,
                    duplicate_events_ignored=duplicate_event_count,
                    replayed_events=replayed_event_count,
                    messages_hydrated=len(hydrated_message_ids),
                    messages_reused=len(reused_message_ids),
                    message_tombstones=tombstone_count,
                    label_mutations=label_mutation_count,
                    durable_receipts=self.receipt_store is not None,
                    committed_checkpoint=committed,
                ),
            )
            return IncrementalHistoryResult(
                run_id=actual_run_id,
                account_id=resolved,
                input_history_id=input_history_id,
                output_history_id=committed.history_id,
                pages_scanned=page_index,
                reused_pages=reused_page_count,
                events_persisted=event_count,
                duplicate_events_ignored=duplicate_event_count,
                replayed_events=replayed_event_count,
                messages_hydrated=len(hydrated_message_ids),
                messages_reused=len(reused_message_ids),
                message_tombstones=tombstone_count,
                label_mutations=label_mutation_count,
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
                    output_history_id=candidate_history_id,
                    pages_scanned=page_index,
                    reused_pages=reused_page_count,
                    events_persisted=event_count,
                    duplicate_events_ignored=duplicate_event_count,
                    replayed_events=replayed_event_count,
                    messages_hydrated=len(hydrated_message_ids),
                    messages_reused=len(reused_message_ids),
                    message_tombstones=tombstone_count,
                    label_mutations=label_mutation_count,
                    durable_receipts=self.receipt_store is not None,
                ),
            )
            raise


def _manifest_payload(
    *,
    run_id: UUID,
    account_id: str,
    started_at: datetime,
    completed_at: datetime | None,
    status: str,
    input_checkpoint: GmailCheckpoint,
    output_history_id: str | None,
    pages_scanned: int,
    reused_pages: int,
    events_persisted: int,
    duplicate_events_ignored: int,
    replayed_events: int,
    messages_hydrated: int,
    messages_reused: int,
    message_tombstones: int,
    label_mutations: int,
    durable_receipts: bool,
    committed_checkpoint: GmailCheckpoint | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": "incremental_history",
        "history_types": list(_SUPPORTED_HISTORY_TYPES),
        "run_id": str(run_id),
        "account_id": account_id,
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat() if completed_at is not None else None,
        "status": status,
        "input_checkpoint": input_checkpoint.history_id,
        "candidate_checkpoint": output_history_id,
        "committed_checkpoint": (
            committed_checkpoint.history_id if committed_checkpoint is not None else None
        ),
        "pages_scanned": pages_scanned,
        "reused_pages": reused_pages,
        "events_persisted": events_persisted,
        "duplicate_events_ignored": duplicate_events_ignored,
        "replayed_events": replayed_events,
        "messages_hydrated": messages_hydrated,
        "messages_reused": messages_reused,
        "message_tombstones": message_tombstones,
        "label_mutations": label_mutations,
        "message_format": "full",
        "durable_receipts": durable_receipts,
    }


def _already_committed_result(
    *,
    manifest_writer: IncrementalManifestWriter,
    run_id: UUID,
    account_id: str,
    committed: GmailCheckpoint,
    previous_manifest: Mapping[str, Any] | None,
    started_at: datetime,
) -> IncrementalHistoryResult:
    input_history_id = (
        _manifest_string(previous_manifest, "input_checkpoint") or committed.history_id
    )
    manifest_path = manifest_writer.write(
        run_id,
        {
            **dict(previous_manifest or {}),
            "schema_version": 1,
            "mode": "incremental_history",
            "run_id": str(run_id),
            "account_id": account_id,
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "status": "RECOVERED_COMMIT",
            "input_checkpoint": input_history_id,
            "candidate_checkpoint": committed.history_id,
            "committed_checkpoint": committed.history_id,
        },
    )
    return _result_from_manifest(
        run_id=run_id,
        account_id=account_id,
        input_history_id=input_history_id,
        committed=committed,
        manifest_path=manifest_path,
        payload=previous_manifest,
        recovered=True,
    )


def _recovered_candidate_result(
    *,
    manifest_writer: IncrementalManifestWriter,
    run_id: UUID,
    account_id: str,
    input_checkpoint: GmailCheckpoint,
    committed: GmailCheckpoint,
    previous_manifest: Mapping[str, Any] | None,
    started_at: datetime,
) -> IncrementalHistoryResult:
    payload = dict(previous_manifest or {})
    payload.update(
        {
            "schema_version": 1,
            "mode": "incremental_history",
            "run_id": str(run_id),
            "account_id": account_id,
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "status": "RECOVERED_COMMIT",
            "input_checkpoint": input_checkpoint.history_id,
            "candidate_checkpoint": committed.history_id,
            "committed_checkpoint": committed.history_id,
        }
    )
    manifest_path = manifest_writer.write(run_id, payload)
    return _result_from_manifest(
        run_id=run_id,
        account_id=account_id,
        input_history_id=input_checkpoint.history_id,
        committed=committed,
        manifest_path=manifest_path,
        payload=previous_manifest,
        recovered=True,
    )


def _result_from_manifest(
    *,
    run_id: UUID,
    account_id: str,
    input_history_id: str,
    committed: GmailCheckpoint,
    manifest_path: str,
    payload: Mapping[str, Any] | None,
    recovered: bool,
) -> IncrementalHistoryResult:
    return IncrementalHistoryResult(
        run_id=run_id,
        account_id=account_id,
        input_history_id=input_history_id,
        output_history_id=committed.history_id,
        pages_scanned=_manifest_count(payload, "pages_scanned"),
        reused_pages=_manifest_count(payload, "reused_pages"),
        events_persisted=_manifest_count(payload, "events_persisted"),
        duplicate_events_ignored=_manifest_count(payload, "duplicate_events_ignored"),
        replayed_events=_manifest_count(payload, "replayed_events"),
        messages_hydrated=_manifest_count(payload, "messages_hydrated"),
        messages_reused=_manifest_count(payload, "messages_reused"),
        message_tombstones=_manifest_count(payload, "message_tombstones"),
        label_mutations=_manifest_count(payload, "label_mutations"),
        committed_checkpoint=committed,
        manifest_path=manifest_path,
        recovered=recovered,
    )


def _manifest_started_at(payload: Mapping[str, Any] | None) -> datetime | None:
    value = _manifest_string(payload, "started_at")
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("incremental manifest started_at must be timezone-aware")
    return parsed


def _manifest_string(payload: Mapping[str, Any] | None, key: str) -> str | None:
    if payload is None:
        return None
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _manifest_count(payload: Mapping[str, Any] | None, key: str) -> int:
    if payload is None:
        return 0
    value = payload.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _best_effort_manifest(
    writer: IncrementalManifestWriter,
    run_id: UUID,
    payload: Mapping[str, Any],
) -> None:
    try:
        writer.write(run_id, payload)
    except Exception:  # noqa: BLE001 - never hide the original incremental failure
        pass
