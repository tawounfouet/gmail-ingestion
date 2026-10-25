"""CLI command for Gmail History incremental synchronization."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.api import GmailApiError
from gmail_ingestion.application import (
    FullResyncRequiredError,
    IncrementalHistorySync,
    MissingCommittedCheckpointError,
)
from gmail_ingestion.auth import CredentialStoreError, GmailAuthError, build_provider
from gmail_ingestion.config import default_credential_dir
from gmail_ingestion.persistence import CheckpointStoreError, LocalCheckpointStore

_DEFAULT_CREDENTIAL_DIR = default_credential_dir()
_DEFAULT_ARTIFACT_ROOT = Path(".gmail-ingestion")
_DEFAULT_CHECKPOINT_ROOT = Path(".gmail-ingestion-control")


def incremental_command(
    account_id: Annotated[
        str | None,
        typer.Option("--account-id", help="Opaque account ID. Defaults to the active account."),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Optional explicit incremental run UUID."),
    ] = None,
    credential_dir: Annotated[
        Path,
        typer.Option("--credential-dir", help="Private local credential store."),
    ] = _DEFAULT_CREDENTIAL_DIR,
    artifact_root: Annotated[
        Path,
        typer.Option("--artifact-root", help="PyIngestKit run-artifact root."),
    ] = _DEFAULT_ARTIFACT_ROOT,
    checkpoint_root: Annotated[
        Path,
        typer.Option("--checkpoint-root", help="Persistent Gmail checkpoint control-plane root."),
    ] = _DEFAULT_CHECKPOINT_ROOT,
    max_results: Annotated[
        int,
        typer.Option("--max-results", min=1, max=500, help="History records per Gmail page."),
    ] = 500,
) -> None:
    """Apply Gmail messageAdded history after the last committed checkpoint."""
    try:
        parsed_run_id = UUID(run_id) if run_id is not None else None
        provider = build_provider(Path("credentials.json"), credential_dir)
        result = IncrementalHistorySync(
            provider,
            LocalArtifactStore(artifact_root),
            LocalCheckpointStore(checkpoint_root),
        ).run(
            account_id,
            run_id=parsed_run_id,
            max_results=max_results,
        )
    except (
        GmailAuthError,
        GmailApiError,
        CredentialStoreError,
        CheckpointStoreError,
        MissingCommittedCheckpointError,
        FullResyncRequiredError,
        OSError,
        ValueError,
    ) as exc:
        typer.echo(f"Incremental sync failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"run_id: {result.run_id}")
    typer.echo(f"account_id: {result.account_id}")
    typer.echo(f"input_checkpoint: {result.input_history_id}")
    typer.echo(f"committed_checkpoint: {result.output_history_id}")
    typer.echo(f"pages_scanned: {result.pages_scanned}")
    typer.echo(f"events_persisted: {result.events_persisted}")
    typer.echo(f"messages_hydrated: {result.messages_hydrated}")
    typer.echo(f"manifest: {result.manifest_path}")
