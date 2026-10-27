"""CLI for local legacy RAW snapshot -> projection baseline bootstrap."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.application.bootstrap_projection_baseline import (
    LegacyBaselineBootstrapError,
    LegacyRawBaselineBootstrapper,
)
from gmail_ingestion.persistence.projection_baseline_writer import ProjectionBaselineWriteError


def bootstrap_baseline_command(
    account_id: Annotated[
        str,
        typer.Option("--account-id", help="Opaque Gmail account ID from the legacy snapshot."),
    ],
    snapshot_run_id: Annotated[
        str,
        typer.Option("--snapshot-run-id", help="Completed 0.4 Full Snapshot run UUID."),
    ],
    email_address: Annotated[
        str,
        typer.Option(
            "--email-address",
            help="Mailbox address stored in the derived accounts row. No Gmail API call is made.",
        ),
    ],
    artifact_root: Annotated[
        Path,
        typer.Option("--artifact-root", help="Artifact root containing the legacy RAW snapshot."),
    ] = Path(".gmail-ingestion"),
    batch_size: Annotated[
        int,
        typer.Option(
            "--batch-size",
            min=1,
            help="RAW messages normalized per streaming batch.",
        ),
    ] = 250,
    progress_every: Annotated[
        int,
        typer.Option(
            "--progress-every",
            min=1,
            help="Print progress after this many processed messages.",
        ),
    ] = 5000,
) -> None:
    """Materialize a projection-compatible normalized baseline from existing RAW artifacts."""
    try:
        parsed_run_id = UUID(snapshot_run_id)
        last_reported = 0

        def progress(processed: int, total: int) -> None:
            nonlocal last_reported
            if processed == total or processed - last_reported >= progress_every:
                typer.echo(f"processed_messages: {processed}/{total}")
                last_reported = processed

        result = LegacyRawBaselineBootstrapper(
            LocalArtifactStore(artifact_root)
        ).run(
            account_id=account_id,
            snapshot_run_id=parsed_run_id,
            email_address=email_address,
            batch_size=batch_size,
            progress=progress,
        )
    except (
        LegacyBaselineBootstrapError,
        ProjectionBaselineWriteError,
        OSError,
        ValueError,
    ) as exc:
        typer.echo(f"Baseline bootstrap failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"account_id: {result.account_id}")
    typer.echo(f"snapshot_run_id: {result.run_id}")
    typer.echo(f"history_id: {result.history_id}")
    typer.echo(f"messages_processed: {result.messages_processed}")
    typer.echo(f"reused: {str(result.reused).lower()}")
    for name, count in sorted(result.row_counts.items()):
        typer.echo(f"rows_{name}: {count}")
    typer.echo(f"manifest: {result.manifest_path}")
