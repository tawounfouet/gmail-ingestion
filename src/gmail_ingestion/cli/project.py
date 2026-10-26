"""CLI command for deterministic current-state projection."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.application import ProjectCurrentState
from gmail_ingestion.persistence import (
    LocalProjectionStore,
    ProjectionBatchReadError,
    ProjectionDatasetError,
    ProjectionStoreError,
)
from gmail_ingestion.projection import (
    ProjectionConvergenceError,
    ProjectionGapError,
)

_DEFAULT_ARTIFACT_ROOT = Path(".gmail-ingestion")
_DEFAULT_PROJECTION_ROOT = Path(".gmail-ingestion-control")


def project_command(
    account_id: Annotated[
        str,
        typer.Option("--account-id", help="Opaque Gmail account ID to project."),
    ],
    baseline_run_id: Annotated[
        str,
        typer.Option(
            "--baseline-run-id",
            help="Qualified normalized baseline run UUID.",
        ),
    ],
    delta_run_ids: Annotated[
        list[str] | None,
        typer.Option(
            "--delta-run-id",
            help=(
                "Committed incremental run UUID. Repeat in checkpoint order from "
                "the baseline frontier through the target frontier."
            ),
        ),
    ] = None,
    baseline_dataset_version: Annotated[
        str | None,
        typer.Option(
            "--baseline-dataset-version",
            help="Baseline dataset identity. Defaults to the baseline run UUID.",
        ),
    ] = None,
    artifact_root: Annotated[
        Path,
        typer.Option("--artifact-root", help="PyIngestKit run-artifact root."),
    ] = _DEFAULT_ARTIFACT_ROOT,
    projection_root: Annotated[
        Path,
        typer.Option(
            "--projection-root",
            help="Persistent projection control/data root.",
        ),
    ] = _DEFAULT_PROJECTION_ROOT,
    candidate_only: Annotated[
        bool,
        typer.Option(
            "--candidate-only/--promote",
            help="Build and qualify without moving the authoritative current pointer.",
        ),
    ] = False,
) -> None:
    """Materialize Gmail Current State from a baseline plus committed History runs."""
    try:
        parsed_baseline_run_id = UUID(baseline_run_id)
        parsed_delta_run_ids = tuple(UUID(value) for value in (delta_run_ids or ()))
        result = ProjectCurrentState(
            LocalArtifactStore(artifact_root),
            LocalProjectionStore(projection_root),
        ).run(
            account_id=account_id,
            baseline_run_id=parsed_baseline_run_id,
            baseline_dataset_version=(
                baseline_dataset_version or str(parsed_baseline_run_id)
            ),
            delta_run_ids=parsed_delta_run_ids,
            candidate_only=candidate_only,
        )
    except (
        ProjectionBatchReadError,
        ProjectionConvergenceError,
        ProjectionDatasetError,
        ProjectionGapError,
        ProjectionStoreError,
        OSError,
        ValueError,
    ) as exc:
        typer.echo(f"Projection failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"account_id: {result.account_id}")
    typer.echo(f"projection_id: {result.projection_id}")
    typer.echo(f"input_history_id: {result.input_history_id}")
    typer.echo(f"target_history_id: {result.target_history_id}")
    typer.echo(f"logical_digest: {result.logical_digest}")
    typer.echo(f"rebuild_digest: {result.rebuild_digest}")
    typer.echo(f"incremental_batches: {result.incremental_batch_count}")
    typer.echo(f"rebuild_batches: {result.rebuild_batch_count}")
    typer.echo(f"promoted: {str(result.promoted).lower()}")
    typer.echo(f"already_current: {str(result.already_current).lower()}")
