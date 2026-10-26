from datetime import UTC, datetime
from uuid import UUID

import pytest

from gmail_ingestion.projection import (
    PROJECTION_SCHEMA_VERSION,
    ProjectionIdentity,
    ProjectionManifest,
    ProjectionPlan,
    ProjectionState,
    ProjectionStatus,
)

ACCOUNT_ID = "acct_0123456789abcdef01234567"
BASELINE_RUN = UUID("00000000-0000-0000-0000-000000000501")
T0 = datetime(2026, 10, 26, 20, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 26, 20, 1, tzinfo=UTC)
T2 = datetime(2026, 10, 26, 20, 2, tzinfo=UTC)


def _identity(target: str = "125") -> ProjectionIdentity:
    return ProjectionIdentity(
        account_id=ACCOUNT_ID,
        baseline_run_id=BASELINE_RUN,
        baseline_dataset_version="baseline-v1",
        projected_checkpoint_history_id=target,
    )


def _plan(target: str = "125") -> ProjectionPlan:
    identity = _identity(target)
    return ProjectionPlan(
        account_id=ACCOUNT_ID,
        projection_id=identity.projection_id,
        baseline_run_id=BASELINE_RUN,
        baseline_dataset_version="baseline-v1",
        input_checkpoint_history_id="100",
        projected_checkpoint_history_id=target,
        predecessor_projection_id=None,
        created_at=T0,
    )


def test_projection_identity_is_deterministic_and_frontier_sensitive() -> None:
    first = _identity()
    second = _identity()
    changed = _identity("126")

    assert first.projection_id == second.projection_id
    assert first.projection_id != changed.projection_id
    assert first.projection_id.startswith("proj_")
    assert len(first.projection_id) == 37


def test_projection_schema_version_is_independent_and_frozen() -> None:
    assert PROJECTION_SCHEMA_VERSION == "1"
    assert _identity().schema_version == "1"


def test_projection_plan_rejects_tampered_identity() -> None:
    with pytest.raises(ValueError, match="deterministic projection identity"):
        ProjectionPlan(
            account_id=ACCOUNT_ID,
            projection_id="proj_00000000000000000000000000000000",
            baseline_run_id=BASELINE_RUN,
            baseline_dataset_version="baseline-v1",
            input_checkpoint_history_id="100",
            projected_checkpoint_history_id="125",
            predecessor_projection_id=None,
            created_at=T0,
        )


def test_projection_state_follows_new_building_ready_lifecycle() -> None:
    plan = _plan()
    state = ProjectionState.new(plan, created_at=T0)

    building = state.start_build(started_at=T1)
    ready = building.mark_ready(logical_digest="sha256:abc", ready_at=T2)

    assert state.status is ProjectionStatus.NEW
    assert building.status is ProjectionStatus.BUILDING
    assert ready.status is ProjectionStatus.READY
    assert ready.logical_digest == "sha256:abc"
    assert ready.ready_at == T2


def test_projection_manifest_tracks_validation_without_new_promotion_status() -> None:
    plan = _plan()
    manifest = ProjectionManifest.from_plan(plan, created_at=T0)
    manifest = manifest.start_build(started_at=T1)
    manifest = manifest.mark_ready(logical_digest="sha256:abc", validated_at=T2)
    promoted = manifest.mark_promoted(promoted_at=T2)

    assert promoted.status is ProjectionStatus.READY
    assert promoted.validated_at == T2
    assert promoted.promoted_at == T2
    assert promoted.logical_digest == "sha256:abc"


def test_projection_timestamps_must_be_timezone_aware() -> None:
    plan = _plan()
    with pytest.raises(ValueError, match="timezone-aware"):
        ProjectionState.new(
            plan,
            created_at=datetime(2026, 10, 26, 20, 0),
        )
