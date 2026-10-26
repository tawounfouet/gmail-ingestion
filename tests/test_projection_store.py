import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from gmail_ingestion.persistence.projection_store import (
    LocalProjectionStore,
    ProjectionConflictError,
    ProjectionStoreError,
)
from gmail_ingestion.projection import ProjectionFoundationService, ProjectionStatus

ACCOUNT_ID = "acct_0123456789abcdef01234567"
BASELINE_RUN = UUID("00000000-0000-0000-0000-000000000501")
T0 = datetime(2026, 10, 26, 20, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 26, 20, 1, tzinfo=UTC)
T2 = datetime(2026, 10, 26, 20, 2, tzinfo=UTC)
T3 = datetime(2026, 10, 26, 20, 3, tzinfo=UTC)
T4 = datetime(2026, 10, 26, 20, 4, tzinfo=UTC)


def _service(tmp_path: Path) -> ProjectionFoundationService:
    return ProjectionFoundationService(LocalProjectionStore(tmp_path / "control"))


def _plan(
    service: ProjectionFoundationService,
    *,
    input_history_id: str = "100",
    target_history_id: str = "125",
    created_at: datetime = T0,
):
    return service.plan(
        account_id=ACCOUNT_ID,
        baseline_run_id=BASELINE_RUN,
        baseline_dataset_version="baseline-v1",
        input_checkpoint_history_id=input_history_id,
        projected_checkpoint_history_id=target_history_id,
        created_at=created_at,
    )


def _ready_candidate(
    service: ProjectionFoundationService,
    *,
    input_history_id: str = "100",
    target_history_id: str = "125",
):
    plan = _plan(
        service,
        input_history_id=input_history_id,
        target_history_id=target_history_id,
    )
    service.create_candidate(plan, created_at=T0)
    service.begin_build(ACCOUNT_ID, plan.projection_id, started_at=T1)
    return service.validate_candidate(
        ACCOUNT_ID,
        plan.projection_id,
        logical_digest=f"digest:{target_history_id}",
        validated_at=T2,
    )


def test_candidate_persistence_does_not_create_current_pointer(tmp_path: Path) -> None:
    service = _service(tmp_path)
    plan = _plan(service)

    candidate = service.create_candidate(plan, created_at=T0)
    reloaded = service.store.load_candidate(ACCOUNT_ID, plan.projection_id)

    assert reloaded == candidate
    assert reloaded is not None
    assert reloaded.state.status is ProjectionStatus.NEW
    assert service.store.load_current(ACCOUNT_ID) is None


def test_candidate_can_be_built_validated_promoted_and_reloaded(tmp_path: Path) -> None:
    service = _service(tmp_path)
    candidate = _ready_candidate(service)

    pointer = service.promote(ACCOUNT_ID, candidate.plan.projection_id, promoted_at=T3)

    assert pointer.projection_id == candidate.plan.projection_id
    assert pointer.projected_checkpoint_history_id == "125"
    assert pointer.promoted_at == T3
    assert service.store.load_candidate(ACCOUNT_ID, candidate.plan.projection_id) is None
    assert service.store.load_current(ACCOUNT_ID) == pointer

    version = service.store.load_version(ACCOUNT_ID, candidate.plan.projection_id)
    assert version is not None
    assert version.state.status is ProjectionStatus.READY
    assert version.manifest.promoted_at == T3
    assert version.manifest.logical_digest == "digest:125"


def test_current_pointer_file_is_the_atomic_publication_boundary(tmp_path: Path) -> None:
    service = _service(tmp_path)
    candidate = _ready_candidate(service)
    service.promote(ACCOUNT_ID, candidate.plan.projection_id, promoted_at=T3)

    current_path = (
        tmp_path
        / "control"
        / "accounts"
        / ACCOUNT_ID
        / "projections"
        / "current.json"
    )
    payload = json.loads(current_path.read_text(encoding="utf-8"))

    assert payload["projection_id"] == candidate.plan.projection_id
    assert payload["projected_checkpoint_history_id"] == "125"
    assert payload["projection_schema_version"] == "1"


def test_promotion_before_validation_is_rejected(tmp_path: Path) -> None:
    service = _service(tmp_path)
    plan = _plan(service)
    service.create_candidate(plan, created_at=T0)

    with pytest.raises(ProjectionStoreError, match="Only READY"):
        service.promote(ACCOUNT_ID, plan.projection_id, promoted_at=T1)

    assert service.store.load_current(ACCOUNT_ID) is None


def test_stale_writer_is_rejected_after_another_candidate_wins(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = _ready_candidate(service)
    first_pointer = service.promote(ACCOUNT_ID, first.plan.projection_id, promoted_at=T3)

    winner_plan = _plan(
        service,
        input_history_id=first_pointer.projected_checkpoint_history_id,
        target_history_id="150",
        created_at=T3,
    )
    stale_plan = _plan(
        service,
        input_history_id=first_pointer.projected_checkpoint_history_id,
        target_history_id="160",
        created_at=T3,
    )

    for plan in (winner_plan, stale_plan):
        service.create_candidate(plan, created_at=T3)
        service.begin_build(ACCOUNT_ID, plan.projection_id, started_at=T3)
        service.validate_candidate(
            ACCOUNT_ID,
            plan.projection_id,
            logical_digest=f"digest:{plan.projected_checkpoint_history_id}",
            validated_at=T3,
        )

    winner = service.promote(ACCOUNT_ID, winner_plan.projection_id, promoted_at=T4)

    with pytest.raises(ProjectionConflictError, match="Current projection changed"):
        service.promote(ACCOUNT_ID, stale_plan.projection_id, promoted_at=T4)

    assert service.store.load_current(ACCOUNT_ID) == winner


def test_planner_requires_continuity_with_current_projection_frontier(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = _ready_candidate(service)
    service.promote(ACCOUNT_ID, first.plan.projection_id, promoted_at=T3)

    with pytest.raises(ValueError, match="current projection frontier"):
        _plan(
            service,
            input_history_id="999",
            target_history_id="1000",
            created_at=T4,
        )


def test_duplicate_candidate_creation_is_idempotent(tmp_path: Path) -> None:
    service = _service(tmp_path)
    plan = _plan(service)
    first = service.create_candidate(plan, created_at=T0)
    second = service.create_candidate(plan, created_at=T0)

    assert second == first


def test_failed_candidate_is_persisted_without_advancing_current(tmp_path: Path) -> None:
    service = _service(tmp_path)
    plan = _plan(service)
    service.create_candidate(plan, created_at=T0)
    service.begin_build(ACCOUNT_ID, plan.projection_id, started_at=T1)

    failed = service.fail_candidate(
        ACCOUNT_ID,
        plan.projection_id,
        "validation failed",
        failed_at=T2,
    )

    assert failed.state.status is ProjectionStatus.FAILED
    assert failed.state.failure_reason == "validation failed"
    assert failed.manifest.failure_reason == "validation failed"
    assert service.store.load_current(ACCOUNT_ID) is None
