"""Reliability orchestration for snapshot bootstrap and History catch-up."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID, uuid4

from pyingestkit.artifacts.base import ArtifactStore

from gmail_ingestion.api import GmailApiClient
from gmail_ingestion.auth.provider import GmailOAuthProvider
from gmail_ingestion.domain import GmailCheckpoint
from gmail_ingestion.persistence import GmailCheckpointStore, GmailHistoryReceiptStore

from .full_sync import FullMessageSnapshot
from .incremental_sync import (
    FullResyncRequiredError,
    IncrementalHistoryResult,
    IncrementalHistorySync,
    MissingCommittedCheckpointError,
)


class FullSnapshotRecoveryError(RuntimeError):
    """Raised when automatic full-snapshot recovery cannot establish a safe baseline."""


class SnapshotResultProtocol(Protocol):
    run_id: UUID
    account_id: str
    history_start: str | None
    manifest_status: str


SnapshotFactory = Callable[..., Any]
ClientFactory = Callable[..., GmailApiClient]


@dataclass(frozen=True, slots=True)
class ReliableIncrementalResult:
    """Outcome of strict incremental sync plus optional full-snapshot recovery."""

    incremental: IncrementalHistoryResult
    recovered_with_full_snapshot: bool
    full_snapshot_run_id: UUID | None = None
    bootstrap_history_id: str | None = None


class ReliableIncrementalSync:
    """Recover a missing/expired cursor using Full Snapshot then deterministic catch-up."""

    def __init__(
        self,
        provider: GmailOAuthProvider,
        artifact_store: ArtifactStore,
        checkpoint_store: GmailCheckpointStore,
        *,
        receipt_store: GmailHistoryReceiptStore | None = None,
        client_factory: ClientFactory = GmailApiClient,
        snapshot_factory: SnapshotFactory = FullMessageSnapshot,
    ) -> None:
        self.provider = provider
        self.artifact_store = artifact_store
        self.checkpoint_store = checkpoint_store
        self.receipt_store = receipt_store
        self.client_factory = client_factory
        self.snapshot_factory = snapshot_factory

    def run(
        self,
        account_id: str | None = None,
        *,
        run_id: UUID | None = None,
        max_results: int = 500,
        auto_full_resync: bool = True,
        snapshot_max_results: int = 500,
        snapshot_max_workers: int = 4,
    ) -> ReliableIncrementalResult:
        core = self._core()
        try:
            incremental = core.run(
                account_id,
                run_id=run_id,
                max_results=max_results,
            )
            return ReliableIncrementalResult(
                incremental=incremental,
                recovered_with_full_snapshot=False,
            )
        except (MissingCommittedCheckpointError, FullResyncRequiredError):
            if not auto_full_resync:
                raise

        snapshot_run_id = uuid4()
        snapshot_runner = self.snapshot_factory(
            self.provider,
            self.artifact_store,
            client_factory=self.client_factory,
        )
        snapshot = snapshot_runner.run(
            account_id,
            run_id=snapshot_run_id,
            max_results=snapshot_max_results,
            max_workers=snapshot_max_workers,
        )
        if snapshot.manifest_status != "COMPLETED":
            raise FullSnapshotRecoveryError(
                "Full snapshot recovery did not complete cleanly; "
                f"status={snapshot.manifest_status!r}"
            )
        if snapshot.history_start is None:
            raise FullSnapshotRecoveryError(
                "Full snapshot recovery did not capture a Gmail history baseline"
            )

        bootstrap = GmailCheckpoint.candidate(
            account_id=snapshot.account_id,
            history_id=snapshot.history_start,
            run_id=snapshot.run_id,
            dataset_version=str(snapshot.run_id),
        )
        self.checkpoint_store.save_candidate(bootstrap)
        self.checkpoint_store.commit(
            bootstrap,
            require_no_committed=True,
        )

        incremental = self._core().run(
            snapshot.account_id,
            run_id=run_id,
            max_results=max_results,
        )
        return ReliableIncrementalResult(
            incremental=incremental,
            recovered_with_full_snapshot=True,
            full_snapshot_run_id=snapshot.run_id,
            bootstrap_history_id=snapshot.history_start,
        )

    def _core(self) -> IncrementalHistorySync:
        return IncrementalHistorySync(
            self.provider,
            self.artifact_store,
            self.checkpoint_store,
            receipt_store=self.receipt_store,
            client_factory=self.client_factory,
        )
