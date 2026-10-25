"""Checkpoint domain model for stateful Gmail synchronization."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

CHECKPOINT_SCHEMA_VERSION = "1"


class CheckpointState(StrEnum):
    """Persistent lifecycle states for a Gmail history checkpoint."""

    CANDIDATE = "CANDIDATE"
    COMMITTED = "COMMITTED"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class GmailCheckpoint:
    """Account-scoped Gmail history cursor with explicit commit semantics."""

    account_id: str
    history_id: str
    run_id: UUID
    state: CheckpointState
    created_at: datetime
    dataset_version: str | None = None
    committed_at: datetime | None = None
    invalidated_at: datetime | None = None
    invalid_reason: str | None = None
    schema_version: str = CHECKPOINT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not self.account_id.strip():
            raise ValueError("account_id must not be empty")
        if not self.history_id.strip():
            raise ValueError("history_id must not be empty")
        if self.schema_version != CHECKPOINT_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported checkpoint schema version: {self.schema_version!r}"
            )
        _require_aware("created_at", self.created_at)
        if self.committed_at is not None:
            _require_aware("committed_at", self.committed_at)
        if self.invalidated_at is not None:
            _require_aware("invalidated_at", self.invalidated_at)

        if self.state is CheckpointState.CANDIDATE:
            if self.committed_at is not None:
                raise ValueError("candidate checkpoint cannot have committed_at")
            if self.invalidated_at is not None or self.invalid_reason is not None:
                raise ValueError("candidate checkpoint cannot be invalidated")
        elif self.state is CheckpointState.COMMITTED:
            if self.committed_at is None:
                raise ValueError("committed checkpoint requires committed_at")
            if self.invalidated_at is not None or self.invalid_reason is not None:
                raise ValueError("committed checkpoint cannot carry invalidation metadata")
        elif self.state is CheckpointState.INVALID:
            if self.committed_at is None:
                raise ValueError("invalid checkpoint must originate from a committed checkpoint")
            if self.invalidated_at is None:
                raise ValueError("invalid checkpoint requires invalidated_at")
            if not self.invalid_reason or not self.invalid_reason.strip():
                raise ValueError("invalid checkpoint requires invalid_reason")

    @classmethod
    def candidate(
        cls,
        *,
        account_id: str,
        history_id: str,
        run_id: UUID,
        dataset_version: str | None = None,
        created_at: datetime | None = None,
    ) -> GmailCheckpoint:
        """Create an uncommitted candidate checkpoint."""
        return cls(
            account_id=account_id,
            history_id=history_id,
            run_id=run_id,
            state=CheckpointState.CANDIDATE,
            created_at=created_at or datetime.now(UTC),
            dataset_version=dataset_version,
        )

    def commit(self, *, committed_at: datetime | None = None) -> GmailCheckpoint:
        """Return a committed copy of this candidate checkpoint."""
        if self.state is not CheckpointState.CANDIDATE:
            raise ValueError("Only candidate checkpoints can be committed")
        return replace(
            self,
            state=CheckpointState.COMMITTED,
            committed_at=committed_at or datetime.now(UTC),
        )

    def invalidate(
        self,
        reason: str,
        *,
        invalidated_at: datetime | None = None,
    ) -> GmailCheckpoint:
        """Return an invalid copy of this committed checkpoint."""
        if self.state is not CheckpointState.COMMITTED:
            raise ValueError("Only committed checkpoints can be invalidated")
        if not reason.strip():
            raise ValueError("reason must not be empty")
        return replace(
            self,
            state=CheckpointState.INVALID,
            invalidated_at=invalidated_at or datetime.now(UTC),
            invalid_reason=reason,
        )


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
