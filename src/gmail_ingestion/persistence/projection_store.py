"""Persistent projection store with atomic current-pointer promotion."""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID

from gmail_ingestion.projection.models import (
    PROJECTION_SCHEMA_VERSION,
    CurrentPointer,
    ProjectionCandidate,
    ProjectionManifest,
    ProjectionPlan,
    ProjectionState,
    ProjectionStatus,
)

_ACCOUNT_ID_RE = re.compile(r"^acct_[0-9a-f]{24}$")


class ProjectionStoreError(RuntimeError):
    """Base error for projection control-plane persistence."""


class ProjectionNotFoundError(ProjectionStoreError):
    """Raised when a requested projection candidate or version does not exist."""


class ProjectionConflictError(ProjectionStoreError):
    """Raised when persisted projection state changed unexpectedly."""


class LocalProjectionStore:
    """Filesystem projection store with immutable versions and atomic pointer publication."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser()
        self.accounts_dir = self.root / "accounts"
        self._ensure_private_dir(self.root)
        self._ensure_private_dir(self.accounts_dir)

    def save_candidate(self, candidate: ProjectionCandidate) -> None:
        """Persist a candidate idempotently without changing the current projection."""
        path = self._candidate_path(candidate.plan.account_id, candidate.plan.projection_id)
        existing = self._load_candidate_optional(path)
        if existing is not None:
            if existing != candidate:
                raise ProjectionConflictError(
                    f"Projection candidate {candidate.plan.projection_id} already exists"
                )
            return
        self._write_candidate(path, candidate)

    def load_candidate(self, account_id: str, projection_id: str) -> ProjectionCandidate | None:
        """Load one account-scoped candidate projection."""
        return self._load_candidate_optional(self._candidate_path(account_id, projection_id))

    def require_candidate(self, account_id: str, projection_id: str) -> ProjectionCandidate:
        """Load a candidate or raise a typed not-found error."""
        candidate = self.load_candidate(account_id, projection_id)
        if candidate is None:
            raise ProjectionNotFoundError(
                f"Projection candidate {projection_id} does not exist for {account_id}"
            )
        return candidate

    def update_candidate(
        self,
        candidate: ProjectionCandidate,
        *,
        expected_status: ProjectionStatus,
    ) -> None:
        """Atomically replace one candidate after checking its previous lifecycle status."""
        path = self._candidate_path(candidate.plan.account_id, candidate.plan.projection_id)
        current = self._load_candidate_optional(path)
        if current is None:
            raise ProjectionNotFoundError(
                f"Projection candidate {candidate.plan.projection_id} does not exist"
            )
        if current.state.status is not expected_status:
            raise ProjectionConflictError(
                "Projection candidate state changed before update: "
                f"expected {expected_status.value!r}, got {current.state.status.value!r}"
            )
        if current.plan != candidate.plan:
            raise ProjectionConflictError("Projection plan is immutable after candidate creation")
        self._write_candidate(path, candidate)

    def discard_candidate(self, account_id: str, projection_id: str) -> None:
        """Discard an unpublished candidate without affecting current or versioned projections."""
        self._candidate_path(account_id, projection_id).unlink(missing_ok=True)
        shutil.rmtree(self._candidate_data_path(account_id, projection_id), ignore_errors=True)

    def candidate_data_dir(self, account_id: str, projection_id: str) -> Path:
        """Return/create the filesystem directory for candidate projection data."""
        path = self._candidate_data_path(account_id, projection_id)
        self._ensure_private_dir(path)
        return path

    def candidate_data_exists(self, account_id: str, projection_id: str) -> bool:
        """Return whether a candidate has a complete projection-data manifest."""
        return (
            self._candidate_data_path(account_id, projection_id) / "projection-data.json"
        ).is_file()

    def version_data_dir(self, account_id: str, projection_id: str) -> Path:
        """Return the immutable promoted data directory for one projection version."""
        path = self._version_data_path(account_id, projection_id)
        if not path.exists():
            raise ProjectionNotFoundError(
                f"Projection data version {projection_id} does not exist for {account_id}"
            )
        return path

    def load_current(self, account_id: str) -> CurrentPointer | None:
        """Return the authoritative current projection pointer for an account."""
        path = self._projection_dir(account_id) / "current.json"
        if not path.exists():
            return None
        payload = self._read_json(path)
        try:
            return _current_pointer_from_payload(payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise ProjectionStoreError(f"Invalid current projection pointer {path}") from exc

    def load_version(self, account_id: str, projection_id: str) -> ProjectionCandidate | None:
        """Load one immutable promoted projection version."""
        return self._load_candidate_optional(self._version_path(account_id, projection_id))

    def promote(
        self,
        account_id: str,
        projection_id: str,
        *,
        promoted_at: datetime | None = None,
    ) -> CurrentPointer:
        """Publish a READY candidate by writing its version before atomically switching current."""
        current = self.load_current(account_id)
        if current is not None and current.projection_id == projection_id:
            version = self.load_version(account_id, projection_id)
            if version is None:
                raise ProjectionStoreError(
                    "Current pointer references a projection version that does not exist"
                )
            self._candidate_path(account_id, projection_id).unlink(missing_ok=True)
            return current

        candidate = self.require_candidate(account_id, projection_id)
        if candidate.state.status is not ProjectionStatus.READY:
            raise ProjectionStoreError("Only READY projection candidates can be promoted")
        if candidate.manifest.status is not ProjectionStatus.READY:
            raise ProjectionStoreError("Only READY projection manifests can be promoted")

        predecessor = candidate.plan.predecessor_projection_id
        if predecessor is None:
            if current is not None:
                raise ProjectionConflictError(
                    "Projection was planned without a predecessor but a current projection "
                    "now exists"
                )
        elif current is None or current.projection_id != predecessor:
            actual = current.projection_id if current is not None else None
            raise ProjectionConflictError(
                "Current projection changed before promotion: "
                f"expected {predecessor!r}, got {actual!r}"
            )

        version_path = self._version_path(account_id, projection_id)
        promoted_candidate = self._load_candidate_optional(version_path)
        if promoted_candidate is None:
            now = promoted_at or datetime.now(UTC)
            promoted_candidate = ProjectionCandidate(
                plan=candidate.plan,
                state=candidate.state,
                manifest=candidate.manifest.mark_promoted(promoted_at=now),
            )
            self._write_candidate(version_path, promoted_candidate)
        elif (
            promoted_candidate.plan != candidate.plan
            or promoted_candidate.state != candidate.state
            or promoted_candidate.manifest.status is not ProjectionStatus.READY
        ):
            raise ProjectionConflictError(
                f"Projection version {projection_id} already exists with different content"
            )

        self._promote_candidate_data(account_id, projection_id)

        promoted_timestamp = promoted_candidate.manifest.promoted_at
        if promoted_timestamp is None:
            raise ProjectionStoreError("Promoted projection version is missing promoted_at")
        pointer = CurrentPointer(
            account_id=account_id,
            projection_id=projection_id,
            baseline_run_id=promoted_candidate.plan.baseline_run_id,
            baseline_dataset_version=promoted_candidate.plan.baseline_dataset_version,
            projected_checkpoint_history_id=(
                promoted_candidate.plan.projected_checkpoint_history_id
            ),
            predecessor_projection_id=predecessor,
            promoted_at=promoted_timestamp,
            schema_version=promoted_candidate.plan.schema_version,
        )
        self._write_private_text(
            self._projection_dir(account_id) / "current.json",
            json.dumps(_current_pointer_to_payload(pointer), indent=2, sort_keys=True) + "\n",
        )
        self._candidate_path(account_id, projection_id).unlink(missing_ok=True)
        return pointer

    def _promote_candidate_data(self, account_id: str, projection_id: str) -> None:
        source = self._candidate_data_path(account_id, projection_id)
        target = self._version_data_path(account_id, projection_id)
        if target.exists():
            if source.exists():
                raise ProjectionConflictError(
                    f"Projection data version {projection_id} already exists"
                )
            return
        if not source.exists():
            return
        self._ensure_private_dir(target.parent)
        os.replace(source, target)

    def _candidate_data_path(self, account_id: str, projection_id: str) -> Path:
        _validate_projection_id(projection_id)
        return self._projection_dir(account_id) / "candidate_data" / projection_id

    def _version_data_path(self, account_id: str, projection_id: str) -> Path:
        _validate_projection_id(projection_id)
        return self._projection_dir(account_id) / "version_data" / projection_id

    def _candidate_path(self, account_id: str, projection_id: str) -> Path:
        _validate_projection_id(projection_id)
        candidates_dir = self._projection_dir(account_id) / "candidates"
        self._ensure_private_dir(candidates_dir)
        return candidates_dir / f"{projection_id}.json"

    def _version_path(self, account_id: str, projection_id: str) -> Path:
        _validate_projection_id(projection_id)
        versions_dir = self._projection_dir(account_id) / "versions"
        self._ensure_private_dir(versions_dir)
        return versions_dir / f"{projection_id}.json"

    def _projection_dir(self, account_id: str) -> Path:
        directory = self.accounts_dir / self._validate_account_id(account_id) / "projections"
        self._ensure_private_dir(directory)
        return directory

    def _load_candidate_optional(self, path: Path) -> ProjectionCandidate | None:
        if not path.exists():
            return None
        payload = self._read_json(path)
        try:
            return _candidate_from_payload(payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise ProjectionStoreError(f"Invalid projection payload {path}") from exc

    def _write_candidate(self, path: Path, candidate: ProjectionCandidate) -> None:
        self._write_private_text(
            path,
            json.dumps(_candidate_to_payload(candidate), indent=2, sort_keys=True) + "\n",
        )

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectionStoreError(f"Unable to read projection state {path}") from exc
        if not isinstance(raw, dict):
            raise ProjectionStoreError(f"Projection state {path} must contain a JSON object")
        return cast(dict[str, Any], raw)

    @staticmethod
    def _validate_account_id(account_id: str) -> str:
        if not _ACCOUNT_ID_RE.fullmatch(account_id):
            raise ProjectionStoreError("Invalid account_id format")
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


def _candidate_to_payload(candidate: ProjectionCandidate) -> dict[str, Any]:
    return {
        "projection_schema_version": candidate.plan.schema_version,
        "plan": _plan_to_payload(candidate.plan),
        "state": _state_to_payload(candidate.state),
        "manifest": _manifest_to_payload(candidate.manifest),
    }


def _candidate_from_payload(payload: Mapping[str, Any]) -> ProjectionCandidate:
    schema_version = _required_string(payload, "projection_schema_version")
    if schema_version != PROJECTION_SCHEMA_VERSION:
        raise ValueError(f"Unsupported projection schema version: {schema_version!r}")
    plan_raw = _required_mapping(payload, "plan")
    state_raw = _required_mapping(payload, "state")
    manifest_raw = _required_mapping(payload, "manifest")
    return ProjectionCandidate(
        plan=_plan_from_payload(plan_raw),
        state=_state_from_payload(state_raw),
        manifest=_manifest_from_payload(manifest_raw),
    )


def _plan_to_payload(plan: ProjectionPlan) -> dict[str, Any]:
    return {
        "account_id": plan.account_id,
        "projection_id": plan.projection_id,
        "baseline_run_id": str(plan.baseline_run_id),
        "baseline_dataset_version": plan.baseline_dataset_version,
        "input_checkpoint_history_id": plan.input_checkpoint_history_id,
        "projected_checkpoint_history_id": plan.projected_checkpoint_history_id,
        "predecessor_projection_id": plan.predecessor_projection_id,
        "created_at": plan.created_at.astimezone(UTC).isoformat(),
        "projection_schema_version": plan.schema_version,
    }


def _state_to_payload(state: ProjectionState) -> dict[str, Any]:
    return {
        "account_id": state.account_id,
        "projection_id": state.projection_id,
        "baseline_run_id": str(state.baseline_run_id),
        "baseline_dataset_version": state.baseline_dataset_version,
        "input_checkpoint_history_id": state.input_checkpoint_history_id,
        "projected_checkpoint_history_id": state.projected_checkpoint_history_id,
        "status": state.status.value,
        "created_at": state.created_at.astimezone(UTC).isoformat(),
        "updated_at": state.updated_at.astimezone(UTC).isoformat(),
        "ready_at": _datetime_to_payload(state.ready_at),
        "failed_at": _datetime_to_payload(state.failed_at),
        "failure_reason": state.failure_reason,
        "logical_digest": state.logical_digest,
        "projection_schema_version": state.schema_version,
    }


def _manifest_to_payload(manifest: ProjectionManifest) -> dict[str, Any]:
    return {
        "account_id": manifest.account_id,
        "projection_id": manifest.projection_id,
        "baseline_run_id": str(manifest.baseline_run_id),
        "baseline_dataset_version": manifest.baseline_dataset_version,
        "input_checkpoint_history_id": manifest.input_checkpoint_history_id,
        "projected_checkpoint_history_id": manifest.projected_checkpoint_history_id,
        "predecessor_projection_id": manifest.predecessor_projection_id,
        "status": manifest.status.value,
        "created_at": manifest.created_at.astimezone(UTC).isoformat(),
        "updated_at": manifest.updated_at.astimezone(UTC).isoformat(),
        "validated_at": _datetime_to_payload(manifest.validated_at),
        "promoted_at": _datetime_to_payload(manifest.promoted_at),
        "failed_at": _datetime_to_payload(manifest.failed_at),
        "failure_reason": manifest.failure_reason,
        "logical_digest": manifest.logical_digest,
        "projection_schema_version": manifest.schema_version,
    }


def _current_pointer_to_payload(pointer: CurrentPointer) -> dict[str, Any]:
    return {
        "projection_schema_version": pointer.schema_version,
        "account_id": pointer.account_id,
        "projection_id": pointer.projection_id,
        "baseline_run_id": str(pointer.baseline_run_id),
        "baseline_dataset_version": pointer.baseline_dataset_version,
        "projected_checkpoint_history_id": pointer.projected_checkpoint_history_id,
        "predecessor_projection_id": pointer.predecessor_projection_id,
        "promoted_at": pointer.promoted_at.astimezone(UTC).isoformat(),
    }


def _plan_from_payload(payload: Mapping[str, Any]) -> ProjectionPlan:
    return ProjectionPlan(
        account_id=_required_string(payload, "account_id"),
        projection_id=_required_string(payload, "projection_id"),
        baseline_run_id=UUID(_required_string(payload, "baseline_run_id")),
        baseline_dataset_version=_required_string(payload, "baseline_dataset_version"),
        input_checkpoint_history_id=_required_string(payload, "input_checkpoint_history_id"),
        projected_checkpoint_history_id=_required_string(
            payload, "projected_checkpoint_history_id"
        ),
        predecessor_projection_id=_optional_string(payload, "predecessor_projection_id"),
        created_at=_required_datetime(payload, "created_at"),
        schema_version=_required_string(payload, "projection_schema_version"),
    )


def _state_from_payload(payload: Mapping[str, Any]) -> ProjectionState:
    return ProjectionState(
        account_id=_required_string(payload, "account_id"),
        projection_id=_required_string(payload, "projection_id"),
        baseline_run_id=UUID(_required_string(payload, "baseline_run_id")),
        baseline_dataset_version=_required_string(payload, "baseline_dataset_version"),
        input_checkpoint_history_id=_required_string(payload, "input_checkpoint_history_id"),
        projected_checkpoint_history_id=_required_string(
            payload, "projected_checkpoint_history_id"
        ),
        status=ProjectionStatus(_required_string(payload, "status")),
        created_at=_required_datetime(payload, "created_at"),
        updated_at=_required_datetime(payload, "updated_at"),
        ready_at=_optional_datetime(payload, "ready_at"),
        failed_at=_optional_datetime(payload, "failed_at"),
        failure_reason=_optional_string(payload, "failure_reason"),
        logical_digest=_optional_string(payload, "logical_digest"),
        schema_version=_required_string(payload, "projection_schema_version"),
    )


def _manifest_from_payload(payload: Mapping[str, Any]) -> ProjectionManifest:
    return ProjectionManifest(
        account_id=_required_string(payload, "account_id"),
        projection_id=_required_string(payload, "projection_id"),
        baseline_run_id=UUID(_required_string(payload, "baseline_run_id")),
        baseline_dataset_version=_required_string(payload, "baseline_dataset_version"),
        input_checkpoint_history_id=_required_string(payload, "input_checkpoint_history_id"),
        projected_checkpoint_history_id=_required_string(
            payload, "projected_checkpoint_history_id"
        ),
        predecessor_projection_id=_optional_string(payload, "predecessor_projection_id"),
        status=ProjectionStatus(_required_string(payload, "status")),
        created_at=_required_datetime(payload, "created_at"),
        updated_at=_required_datetime(payload, "updated_at"),
        validated_at=_optional_datetime(payload, "validated_at"),
        promoted_at=_optional_datetime(payload, "promoted_at"),
        failed_at=_optional_datetime(payload, "failed_at"),
        failure_reason=_optional_string(payload, "failure_reason"),
        logical_digest=_optional_string(payload, "logical_digest"),
        schema_version=_required_string(payload, "projection_schema_version"),
    )


def _current_pointer_from_payload(payload: Mapping[str, Any]) -> CurrentPointer:
    return CurrentPointer(
        account_id=_required_string(payload, "account_id"),
        projection_id=_required_string(payload, "projection_id"),
        baseline_run_id=UUID(_required_string(payload, "baseline_run_id")),
        baseline_dataset_version=_required_string(payload, "baseline_dataset_version"),
        projected_checkpoint_history_id=_required_string(
            payload, "projected_checkpoint_history_id"
        ),
        predecessor_projection_id=_optional_string(payload, "predecessor_projection_id"),
        promoted_at=_required_datetime(payload, "promoted_at"),
        schema_version=_required_string(payload, "projection_schema_version"),
    )


def _required_mapping(payload: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    value = payload[key]
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object")
    return cast(Mapping[str, Any], value)


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


def _validate_projection_id(value: str) -> None:
    if not re.fullmatch(r"proj_[0-9a-f]{32}", value):
        raise ProjectionStoreError("Invalid projection_id format")
