"""Projection control-plane models for Gmail current-state materialization."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

PROJECTION_SCHEMA_VERSION = "1"
_PROJECTION_ID_RE = re.compile(r"^proj_[0-9a-f]{32}$")


class ProjectionStatus(StrEnum):
    """Lifecycle states for one materialized-current-state projection candidate."""

    NEW = "NEW"
    BUILDING = "BUILDING"
    READY = "READY"
    FAILED = "FAILED"
    STALE = "STALE"


@dataclass(frozen=True, slots=True)
class ProjectionIdentity:
    """Deterministic identity inputs for one logical projection frontier."""

    account_id: str
    baseline_run_id: UUID
    baseline_dataset_version: str
    projected_checkpoint_history_id: str
    schema_version: str = PROJECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_non_empty("account_id", self.account_id)
        _require_non_empty("baseline_dataset_version", self.baseline_dataset_version)
        _require_non_empty(
            "projected_checkpoint_history_id", self.projected_checkpoint_history_id
        )
        _require_supported_schema(self.schema_version)

    @property
    def projection_id(self) -> str:
        """Return a stable identifier derived only from logical projection inputs."""
        payload = {
            "account_id": self.account_id,
            "baseline_run_id": str(self.baseline_run_id),
            "baseline_dataset_version": self.baseline_dataset_version,
            "projected_checkpoint_history_id": self.projected_checkpoint_history_id,
            "projection_schema_version": self.schema_version,
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return f"proj_{digest[:32]}"


@dataclass(frozen=True, slots=True)
class ProjectionPlan:
    """Immutable plan describing one candidate projection build."""

    account_id: str
    projection_id: str
    baseline_run_id: UUID
    baseline_dataset_version: str
    input_checkpoint_history_id: str
    projected_checkpoint_history_id: str
    predecessor_projection_id: str | None
    created_at: datetime
    schema_version: str = PROJECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_non_empty("account_id", self.account_id)
        _require_projection_id(self.projection_id)
        _require_non_empty("baseline_dataset_version", self.baseline_dataset_version)
        _require_non_empty("input_checkpoint_history_id", self.input_checkpoint_history_id)
        _require_non_empty(
            "projected_checkpoint_history_id", self.projected_checkpoint_history_id
        )
        if self.predecessor_projection_id is not None:
            _require_projection_id(self.predecessor_projection_id)
        _require_aware("created_at", self.created_at)
        _require_supported_schema(self.schema_version)

        expected = ProjectionIdentity(
            account_id=self.account_id,
            baseline_run_id=self.baseline_run_id,
            baseline_dataset_version=self.baseline_dataset_version,
            projected_checkpoint_history_id=self.projected_checkpoint_history_id,
            schema_version=self.schema_version,
        ).projection_id
        if self.projection_id != expected:
            raise ValueError(
                "projection_id does not match the deterministic projection identity"
            )


@dataclass(frozen=True, slots=True)
class ProjectionState:
    """Account-scoped lifecycle state for one projection candidate."""

    account_id: str
    projection_id: str
    baseline_run_id: UUID
    baseline_dataset_version: str
    input_checkpoint_history_id: str
    projected_checkpoint_history_id: str
    status: ProjectionStatus
    created_at: datetime
    updated_at: datetime
    ready_at: datetime | None = None
    failed_at: datetime | None = None
    failure_reason: str | None = None
    logical_digest: str | None = None
    schema_version: str = PROJECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_non_empty("account_id", self.account_id)
        _require_projection_id(self.projection_id)
        _require_non_empty("baseline_dataset_version", self.baseline_dataset_version)
        _require_non_empty("input_checkpoint_history_id", self.input_checkpoint_history_id)
        _require_non_empty(
            "projected_checkpoint_history_id", self.projected_checkpoint_history_id
        )
        _require_aware("created_at", self.created_at)
        _require_aware("updated_at", self.updated_at)
        if self.ready_at is not None:
            _require_aware("ready_at", self.ready_at)
        if self.failed_at is not None:
            _require_aware("failed_at", self.failed_at)
        _require_supported_schema(self.schema_version)

        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if self.status is ProjectionStatus.READY:
            if self.ready_at is None:
                raise ValueError("READY projection requires ready_at")
            if self.failed_at is not None or self.failure_reason is not None:
                raise ValueError("READY projection cannot carry failure metadata")
        elif self.status is ProjectionStatus.FAILED:
            if self.failed_at is None:
                raise ValueError("FAILED projection requires failed_at")
            if not self.failure_reason or not self.failure_reason.strip():
                raise ValueError("FAILED projection requires failure_reason")
            if self.ready_at is not None:
                raise ValueError("FAILED projection cannot be ready")
        else:
            if self.failed_at is not None or self.failure_reason is not None:
                raise ValueError(f"{self.status} projection cannot carry failure metadata")
            if self.status is not ProjectionStatus.STALE and self.ready_at is not None:
                raise ValueError(f"{self.status} projection cannot have ready_at")

    @classmethod
    def new(cls, plan: ProjectionPlan, *, created_at: datetime | None = None) -> ProjectionState:
        """Create the initial NEW state for a persisted projection candidate."""
        now = created_at or datetime.now(UTC)
        return cls(
            account_id=plan.account_id,
            projection_id=plan.projection_id,
            baseline_run_id=plan.baseline_run_id,
            baseline_dataset_version=plan.baseline_dataset_version,
            input_checkpoint_history_id=plan.input_checkpoint_history_id,
            projected_checkpoint_history_id=plan.projected_checkpoint_history_id,
            status=ProjectionStatus.NEW,
            created_at=now,
            updated_at=now,
            schema_version=plan.schema_version,
        )

    def start_build(self, *, started_at: datetime | None = None) -> ProjectionState:
        """Transition NEW -> BUILDING."""
        if self.status is not ProjectionStatus.NEW:
            raise ValueError("Only NEW projections can start building")
        now = started_at or datetime.now(UTC)
        return replace(self, status=ProjectionStatus.BUILDING, updated_at=now)

    def mark_ready(
        self,
        *,
        logical_digest: str | None = None,
        ready_at: datetime | None = None,
    ) -> ProjectionState:
        """Transition BUILDING -> READY after validation succeeds."""
        if self.status is not ProjectionStatus.BUILDING:
            raise ValueError("Only BUILDING projections can become READY")
        if logical_digest is not None:
            _require_non_empty("logical_digest", logical_digest)
        now = ready_at or datetime.now(UTC)
        return replace(
            self,
            status=ProjectionStatus.READY,
            updated_at=now,
            ready_at=now,
            logical_digest=logical_digest,
        )

    def fail(self, reason: str, *, failed_at: datetime | None = None) -> ProjectionState:
        """Transition NEW/BUILDING -> FAILED while preserving the last good current pointer."""
        if self.status not in {ProjectionStatus.NEW, ProjectionStatus.BUILDING}:
            raise ValueError("Only NEW or BUILDING projections can fail")
        _require_non_empty("reason", reason)
        now = failed_at or datetime.now(UTC)
        return replace(
            self,
            status=ProjectionStatus.FAILED,
            updated_at=now,
            failed_at=now,
            failure_reason=reason,
        )

    def stale(self, *, stale_at: datetime | None = None) -> ProjectionState:
        """Transition READY -> STALE when a newer authoritative projection supersedes it."""
        if self.status is not ProjectionStatus.READY:
            raise ValueError("Only READY projections can become STALE")
        now = stale_at or datetime.now(UTC)
        return replace(self, status=ProjectionStatus.STALE, updated_at=now)


@dataclass(frozen=True, slots=True)
class ProjectionManifest:
    """Auditable metadata describing one projection candidate or promoted version."""

    account_id: str
    projection_id: str
    baseline_run_id: UUID
    baseline_dataset_version: str
    input_checkpoint_history_id: str
    projected_checkpoint_history_id: str
    predecessor_projection_id: str | None
    status: ProjectionStatus
    created_at: datetime
    updated_at: datetime
    validated_at: datetime | None = None
    promoted_at: datetime | None = None
    failed_at: datetime | None = None
    failure_reason: str | None = None
    logical_digest: str | None = None
    schema_version: str = PROJECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_non_empty("account_id", self.account_id)
        _require_projection_id(self.projection_id)
        _require_non_empty("baseline_dataset_version", self.baseline_dataset_version)
        _require_non_empty("input_checkpoint_history_id", self.input_checkpoint_history_id)
        _require_non_empty(
            "projected_checkpoint_history_id", self.projected_checkpoint_history_id
        )
        if self.predecessor_projection_id is not None:
            _require_projection_id(self.predecessor_projection_id)
        _require_aware("created_at", self.created_at)
        _require_aware("updated_at", self.updated_at)
        if self.validated_at is not None:
            _require_aware("validated_at", self.validated_at)
        if self.promoted_at is not None:
            _require_aware("promoted_at", self.promoted_at)
        if self.failed_at is not None:
            _require_aware("failed_at", self.failed_at)
        if self.logical_digest is not None:
            _require_non_empty("logical_digest", self.logical_digest)
        _require_supported_schema(self.schema_version)

        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")
        if self.status is ProjectionStatus.READY:
            if self.validated_at is None:
                raise ValueError("READY projection manifest requires validated_at")
            if self.failed_at is not None or self.failure_reason is not None:
                raise ValueError("READY projection manifest cannot carry failure metadata")
        elif self.status is ProjectionStatus.FAILED:
            if self.failed_at is None:
                raise ValueError("FAILED projection manifest requires failed_at")
            if not self.failure_reason or not self.failure_reason.strip():
                raise ValueError("FAILED projection manifest requires failure_reason")
        elif self.failed_at is not None or self.failure_reason is not None:
            raise ValueError(f"{self.status} manifest cannot carry failure metadata")
        if self.promoted_at is not None and self.status is not ProjectionStatus.READY:
            raise ValueError("Only READY projection manifests can be promoted")

    @classmethod
    def from_plan(
        cls,
        plan: ProjectionPlan,
        *,
        created_at: datetime | None = None,
    ) -> ProjectionManifest:
        """Create a NEW manifest from a deterministic projection plan."""
        now = created_at or datetime.now(UTC)
        return cls(
            account_id=plan.account_id,
            projection_id=plan.projection_id,
            baseline_run_id=plan.baseline_run_id,
            baseline_dataset_version=plan.baseline_dataset_version,
            input_checkpoint_history_id=plan.input_checkpoint_history_id,
            projected_checkpoint_history_id=plan.projected_checkpoint_history_id,
            predecessor_projection_id=plan.predecessor_projection_id,
            status=ProjectionStatus.NEW,
            created_at=now,
            updated_at=now,
            schema_version=plan.schema_version,
        )

    def start_build(self, *, started_at: datetime | None = None) -> ProjectionManifest:
        """Mirror the candidate lifecycle transition NEW -> BUILDING."""
        if self.status is not ProjectionStatus.NEW:
            raise ValueError("Only NEW projection manifests can start building")
        now = started_at or datetime.now(UTC)
        return replace(self, status=ProjectionStatus.BUILDING, updated_at=now)

    def mark_ready(
        self,
        *,
        logical_digest: str | None = None,
        validated_at: datetime | None = None,
    ) -> ProjectionManifest:
        """Mirror the candidate lifecycle transition BUILDING -> READY."""
        if self.status is not ProjectionStatus.BUILDING:
            raise ValueError("Only BUILDING projection manifests can become READY")
        if logical_digest is not None:
            _require_non_empty("logical_digest", logical_digest)
        now = validated_at or datetime.now(UTC)
        return replace(
            self,
            status=ProjectionStatus.READY,
            updated_at=now,
            validated_at=now,
            logical_digest=logical_digest,
        )

    def fail(
        self,
        reason: str,
        *,
        failed_at: datetime | None = None,
    ) -> ProjectionManifest:
        """Mirror the candidate lifecycle transition NEW/BUILDING -> FAILED."""
        if self.status not in {ProjectionStatus.NEW, ProjectionStatus.BUILDING}:
            raise ValueError("Only NEW or BUILDING projection manifests can fail")
        _require_non_empty("reason", reason)
        now = failed_at or datetime.now(UTC)
        return replace(
            self,
            status=ProjectionStatus.FAILED,
            updated_at=now,
            failed_at=now,
            failure_reason=reason,
        )

    def mark_promoted(self, *, promoted_at: datetime | None = None) -> ProjectionManifest:
        """Record successful atomic publication without introducing a new lifecycle status."""
        if self.status is not ProjectionStatus.READY:
            raise ValueError("Only READY projection manifests can be promoted")
        now = promoted_at or datetime.now(UTC)
        return replace(self, updated_at=now, promoted_at=now)


@dataclass(frozen=True, slots=True)
class ProjectionCandidate:
    """Atomic persistence aggregate for plan, state and manifest."""

    plan: ProjectionPlan
    state: ProjectionState
    manifest: ProjectionManifest

    def __post_init__(self) -> None:
        values = (
            self.plan.account_id,
            self.state.account_id,
            self.manifest.account_id,
        )
        if len(set(values)) != 1:
            raise ValueError("projection candidate account_id values must match")
        ids = (
            self.plan.projection_id,
            self.state.projection_id,
            self.manifest.projection_id,
        )
        if len(set(ids)) != 1:
            raise ValueError("projection candidate projection_id values must match")
        schemas = (
            self.plan.schema_version,
            self.state.schema_version,
            self.manifest.schema_version,
        )
        if len(set(schemas)) != 1:
            raise ValueError("projection candidate schema versions must match")
        if self.state.status is not self.manifest.status:
            raise ValueError("projection state and manifest status must match")
        for field_name in (
            "baseline_run_id",
            "baseline_dataset_version",
            "input_checkpoint_history_id",
            "projected_checkpoint_history_id",
        ):
            expected = getattr(self.plan, field_name)
            if getattr(self.state, field_name) != expected:
                raise ValueError(f"projection state {field_name} must match plan")
            if getattr(self.manifest, field_name) != expected:
                raise ValueError(f"projection manifest {field_name} must match plan")
        if self.manifest.predecessor_projection_id != self.plan.predecessor_projection_id:
            raise ValueError("projection manifest predecessor must match plan")
        if self.state.logical_digest != self.manifest.logical_digest:
            raise ValueError("projection logical digest must match between state and manifest")
        if self.state.failure_reason != self.manifest.failure_reason:
            raise ValueError("projection failure reason must match between state and manifest")


@dataclass(frozen=True, slots=True)
class CurrentPointer:
    """Small atomic pointer to the authoritative promoted projection version."""

    account_id: str
    projection_id: str
    baseline_run_id: UUID
    baseline_dataset_version: str
    projected_checkpoint_history_id: str
    predecessor_projection_id: str | None
    promoted_at: datetime
    schema_version: str = PROJECTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _require_non_empty("account_id", self.account_id)
        _require_projection_id(self.projection_id)
        _require_non_empty("baseline_dataset_version", self.baseline_dataset_version)
        _require_non_empty(
            "projected_checkpoint_history_id", self.projected_checkpoint_history_id
        )
        if self.predecessor_projection_id is not None:
            _require_projection_id(self.predecessor_projection_id)
        _require_aware("promoted_at", self.promoted_at)
        _require_supported_schema(self.schema_version)


def _require_non_empty(name: str, value: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _require_projection_id(value: str) -> None:
    if not _PROJECTION_ID_RE.fullmatch(value):
        raise ValueError("projection_id must match proj_<32 lowercase hex chars>")


def _require_supported_schema(value: str) -> None:
    if value != PROJECTION_SCHEMA_VERSION:
        raise ValueError(f"Unsupported projection schema version: {value!r}")


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
