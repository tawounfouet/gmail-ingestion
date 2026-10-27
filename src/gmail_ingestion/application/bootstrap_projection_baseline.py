"""Bootstrap a projection-compatible normalized baseline from a legacy RAW snapshot."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from pyingestkit.artifacts.base import ArtifactStore

from gmail_ingestion._version import __version__
from gmail_ingestion.control import MessageDiscoveryQueue
from gmail_ingestion.domain import GmailAccount, MessageFetchStatus
from gmail_ingestion.normalization import normalize_snapshot
from gmail_ingestion.persistence.projection_baseline_writer import (
    ProjectionBaselineParquetWriter,
    ProjectionBaselineWriteError,
    ProjectionBaselineWriteResult,
)
from gmail_ingestion.persistence.raw_message_writer import RawMessageWriter
from gmail_ingestion.projection.message_delta import MESSAGE_PROJECTION_TABLES

ProgressCallback = Callable[[int, int], None]


class LegacyBaselineBootstrapError(RuntimeError):
    """Raised when a legacy RAW snapshot cannot safely seed the projection baseline."""


@dataclass(frozen=True, slots=True)
class LegacyBaselineBootstrapResult:
    """Outcome of one local RAW -> projection-baseline bootstrap."""

    run_id: UUID
    account_id: str
    history_id: str
    messages_processed: int
    row_counts: Mapping[str, int]
    manifest_path: str
    reused: bool


class LegacyRawBaselineBootstrapper:
    """Transform a completed 0.4 RAW snapshot into projection-compatible Parquet locally."""

    def __init__(
        self,
        artifact_store: ArtifactStore,
        *,
        writer: ProjectionBaselineParquetWriter | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.writer = writer or ProjectionBaselineParquetWriter(artifact_store)
        self.raw_messages = RawMessageWriter(artifact_store)

    def run(
        self,
        *,
        account_id: str,
        snapshot_run_id: UUID,
        email_address: str,
        batch_size: int = 250,
        progress: ProgressCallback | None = None,
    ) -> LegacyBaselineBootstrapResult:
        """Build a stable projection baseline without any Gmail API call."""
        if not email_address or "@" not in email_address:
            raise LegacyBaselineBootstrapError("email_address must be a valid mailbox address")
        if batch_size < 1:
            raise LegacyBaselineBootstrapError("batch_size must be >= 1")

        queue = MessageDiscoveryQueue.load_or_create(
            self.artifact_store,
            snapshot_run_id,
            account_id,
            None,
        )
        if not queue.loaded_existing:
            raise LegacyBaselineBootstrapError(
                f"Snapshot discovery state does not exist for run {snapshot_run_id}"
            )
        if not queue.discovery_complete:
            raise LegacyBaselineBootstrapError("Snapshot discovery is not complete")
        if queue.history_start is None:
            raise LegacyBaselineBootstrapError("Snapshot has no Gmail History baseline")
        if queue.failed_count:
            raise LegacyBaselineBootstrapError(
                f"Snapshot contains {queue.failed_count} failed message fetches"
            )

        records = sorted(queue.records.values(), key=lambda record: record.message_id)
        if not records:
            raise LegacyBaselineBootstrapError("Snapshot contains no discovered messages")
        not_fetched = [
            record.message_id
            for record in records
            if record.fetch_status is not MessageFetchStatus.FETCHED
        ]
        if not_fetched:
            raise LegacyBaselineBootstrapError(
                f"Snapshot has {len(not_fetched)} messages without FETCHED RAW evidence"
            )

        account = GmailAccount(
            account_id=account_id,
            email_address=email_address,
            messages_total=len(records),
            threads_total=len({record.thread_id for record in records}),
            history_id=queue.history_start,
        )
        ingested_at = min(record.discovered_at for record in records)
        account_row = normalize_snapshot(
            account,
            (),
            (),
            ingested_at=ingested_at,
            dataset_version=str(snapshot_run_id),
            source_connector_version=__version__,
        ).rows("accounts")[0]

        processed = 0

        def batches() -> Iterator[Mapping[str, Sequence[Mapping[str, Any]]]]:
            nonlocal processed
            for start in range(0, len(records), batch_size):
                chunk = records[start : start + batch_size]
                payloads: list[Mapping[str, Any]] = []
                for record in chunk:
                    payload = self.raw_messages.read(snapshot_run_id, record.message_id)
                    if payload.get("threadId") != record.thread_id:
                        raise LegacyBaselineBootstrapError(
                            f"RAW message {record.message_id!r} threadId differs from discovery"
                        )
                    payloads.append(payload)

                normalized = normalize_snapshot(
                    account,
                    (),
                    payloads,
                    ingested_at=ingested_at,
                    dataset_version=str(snapshot_run_id),
                    source_connector_version=__version__,
                )
                processed += len(chunk)
                if progress is not None:
                    progress(processed, len(records))
                yield {
                    name: normalized.rows(name)
                    for name in MESSAGE_PROJECTION_TABLES
                }

        try:
            result: ProjectionBaselineWriteResult = self.writer.write(
                run_id=snapshot_run_id,
                account_id=account_id,
                history_id=queue.history_start,
                account_row=account_row,
                batches=batches(),
                dataset_version=str(snapshot_run_id),
                source_connector_version=__version__,
            )
        except (OSError, ProjectionBaselineWriteError, ValueError) as exc:
            raise LegacyBaselineBootstrapError(str(exc)) from exc

        if result.reused:
            processed = int(result.row_counts.get("messages", 0))

        return LegacyBaselineBootstrapResult(
            run_id=result.run_id,
            account_id=result.account_id,
            history_id=result.history_id,
            messages_processed=processed,
            row_counts=result.row_counts,
            manifest_path=str(result.manifest_path),
            reused=result.reused,
        )
