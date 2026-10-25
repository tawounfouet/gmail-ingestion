"""Persistent checkpoint store for Gmail incremental synchronization."""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, cast
from uuid import UUID

from gmail_ingestion.domain import CheckpointState, GmailCheckpoint

_ACCOUNT_ID_RE = re.compile(r"^acct_[0-9a-f]{24}$")


class CheckpointStoreError(RuntimeError):
    """Base error for checkpoint persistence."""


class CheckpointNotFoundError(CheckpointStoreError):
    """Raised when a requested checkpoint does not exist."""


class CheckpointConflictError(CheckpointStoreError):
    """Raised when checkpoint state changed unexpectedly."""


class GmailCheckpointStore(Protocol):
    """Persistence contract for Gmail checkpoints."""

    def load_committed(self, account_id: str) -> GmailCheckpoint | None: ...

    def save_candidate(self, checkpoint: GmailCheckpoint) -> None: ...

    def load_candidate(self, account_id: str, run_id: UUID) -> GmailCheckpoint | None: ...

    def commit(
        self,
        checkpoint: GmailCheckpoint,
        *,
        committed_at: datetime | None = None,
    ) -> GmailCheckpoint: ...

    def discard_candidate(self, account_id: str, run_id: UUID) -> None: ...

    def invalidate(
        self,
        account_id: str,
        reason: str,
        *,
        invalidated_at: datetime | None = None,
        expected_history_id: str | None = None,
    ) -> GmailCheckpoint: ...


CheckpointStore = GmailCheckpointStore


class LocalCheckpointStore:
    """Atomic filesystem checkpoint store isolated by Gmail account."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser()
        self.accounts_dir = self.root / "accounts"
        self._ensure_private_dir(self.root)
        self._ensure_private_dir(self.accounts_dir)

    def load_committed(self, account_id: str) -> GmailCheckpoint | None:
        """Return the last confirmed checkpoint for an account."""
        path = self._checkpoint_dir(account_id) / "committed.json"
        return self._load_optional(path)

    def save_candidate(self, checkpoint: GmailCheckpoint) -> None:
        """Persist a candidate without changing the last committed checkpoint."""
        if checkpoint.state is not CheckpointState.CANDIDATE:
            raise CheckpointStoreError("save_candidate requires a CANDIDATE checkpoint")
        path = self._candidate_path(checkpoint.account_id, checkpoint.run_id)
        existing = self._load_optional(path)
        if existing is not None and existing != checkpoint:
            raise CheckpointConflictError(
                f"Candidate checkpoint already exists for run {checkpoint.run_id}"
            )
        self._write_checkpoint(path, checkpoint)

    def load_candidate(self, account_id: str, run_id: UUID) -> GmailCheckpoint | None:
        """Load one run-scoped candidate checkpoint."""
        return self._load_optional(self._candidate_path(account_id, run_id))

    def commit(
        self,
        checkpoint: GmailCheckpoint,
        *,
        committed_at: datetime | None = None,
    ) -> GmailCheckpoint:
        """Atomically promote a persisted candidate to the committed pointer."""
        if checkpoint.state is not CheckpointState.CANDIDATE:
            raise CheckpointStoreError("commit requires a CANDIDATE checkpoint")
        candidate_path = self._candidate_path(checkpoint.account_id, checkpoint.run_id)
        persisted = self._load_optional(candidate_path)
        if persisted is None:
            raise CheckpointNotFoundError(
                f"No candidate checkpoint exists for run {checkpoint.run_id}"
            )
        if persisted != checkpoint:
            raise CheckpointConflictError(
                f"Persisted candidate differs for run {checkpoint.run_id}"
            )

        committed = checkpoint.commit(committed_at=committed_at)
        committed_path = self._checkpoint_dir(checkpoint.account_id) / "committed.json"
        self._write_checkpoint(committed_path, committed)
        candidate_path.unlink(missing_ok=True)
        return committed

    def discard_candidate(self, account_id: str, run_id: UUID) -> None:
        """Discard only the candidate for one run, preserving committed state."""
        self._candidate_path(account_id, run_id).unlink(missing_ok=True)

    def invalidate(
        self,
        account_id: str,
        reason: str,
        *,
        invalidated_at: datetime | None = None,
        expected_history_id: str | None = None,
    ) -> GmailCheckpoint:
        """Invalidate the current committed checkpoint with optional CAS protection."""
        committed_path = self._checkpoint_dir(account_id) / "committed.json"
        committed = self._load_optional(committed_path)
        if committed is None:
            raise CheckpointNotFoundError(f"No committed checkpoint exists for {account_id}")
        if expected_history_id is not None and committed.history_id != expected_history_id:
            raise CheckpointConflictError(
                "Committed checkpoint changed before invalidation: "
                f"expected {expected_history_id!r}, got {committed.history_id!r}"
            )

        invalid = committed.invalidate(reason, invalidated_at=invalidated_at)
        invalid_path = self._checkpoint_dir(account_id) / "invalid.json"
        self._write_checkpoint(invalid_path, invalid)
        committed_path.unlink(missing_ok=True)
        return invalid

    def load_invalid(self, account_id: str) -> GmailCheckpoint | None:
        """Return the most recently invalidated committed checkpoint."""
        return self._load_optional(self._checkpoint_dir(account_id) / "invalid.json")

    def _candidate_path(self, account_id: str, run_id: UUID) -> Path:
        candidate_dir = self._checkpoint_dir(account_id) / "candidates"
        self._ensure_private_dir(candidate_dir)
        return candidate_dir / f"{run_id}.json"

    def _checkpoint_dir(self, account_id: str) -> Path:
        checkpoint_dir = self.accounts_dir / self._validate_account_id(account_id) / "checkpoints"
        self._ensure_private_dir(checkpoint_dir)
        return checkpoint_dir

    def _load_optional(self, path: Path) -> GmailCheckpoint | None:
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckpointStoreError(f"Unable to read checkpoint {path}") from exc
        if not isinstance(raw, dict):
            raise CheckpointStoreError(f"Checkpoint {path} must contain a JSON object")
        return _checkpoint_from_payload(cast(dict[str, Any], raw))

    def _write_checkpoint(self, path: Path, checkpoint: GmailCheckpoint) -> None:
        payload = _checkpoint_to_payload(checkpoint)
        self._write_private_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    @staticmethod
    def _validate_account_id(account_id: str) -> str:
        if not _ACCOUNT_ID_RE.fullmatch(account_id):
            raise CheckpointStoreError("Invalid account_id format")
        return account_id

    @staticmethod
    def _ensure_private_dir(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            path.chmod(0o700)
        except OSError:
            pass

    @staticmethod
    def _write_private_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        finally:
            if temp.exists():
                temp.unlink()


def _checkpoint_to_payload(checkpoint: GmailCheckpoint) -> dict[str, str | None]:
    return {
        "checkpoint_schema_version": checkpoint.schema_version,
        "account_id": checkpoint.account_id,
        "history_id": checkpoint.history_id,
        "run_id": str(checkpoint.run_id),
        "dataset_version": checkpoint.dataset_version,
        "state": checkpoint.state.value,
        "created_at": checkpoint.created_at.astimezone(UTC).isoformat(),
        "committed_at": _datetime_to_payload(checkpoint.committed_at),
        "invalidated_at": _datetime_to_payload(checkpoint.invalidated_at),
        "invalid_reason": checkpoint.invalid_reason,
    }


def _checkpoint_from_payload(payload: Mapping[str, Any]) -> GmailCheckpoint:
    try:
        schema_version = _required_string(payload, "checkpoint_schema_version")
        account_id = _required_string(payload, "account_id")
        history_id = _required_string(payload, "history_id")
        run_id = UUID(_required_string(payload, "run_id"))
        state = CheckpointState(_required_string(payload, "state"))
        created_at = _required_datetime(payload, "created_at")
        dataset_version = _optional_string(payload, "dataset_version")
        committed_at = _optional_datetime(payload, "committed_at")
        invalidated_at = _optional_datetime(payload, "invalidated_at")
        invalid_reason = _optional_string(payload, "invalid_reason")
        return GmailCheckpoint(
            account_id=account_id,
            history_id=history_id,
            run_id=run_id,
            state=state,
            created_at=created_at,
            dataset_version=dataset_version,
            committed_at=committed_at,
            invalidated_at=invalidated_at,
            invalid_reason=invalid_reason,
            schema_version=schema_version,
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CheckpointStoreError("Invalid checkpoint payload") from exc


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null")
    return value


def _required_datetime(payload: Mapping[str, Any], key: str) -> datetime:
    value = _optional_datetime(payload, key)
    if value is None:
        raise ValueError(f"{key} must be a datetime")
    return value


def _optional_datetime(payload: Mapping[str, Any], key: str) -> datetime | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be an ISO datetime string or null")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{key} must be timezone-aware")
    return parsed


def _datetime_to_payload(value: datetime | None) -> str | None:
    return value.astimezone(UTC).isoformat() if value is not None else None
