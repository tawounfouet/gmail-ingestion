"""CLI command for crash-safe Gmail History incremental synchronization."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.api import GmailApiError
from gmail_ingestion.application import (
    FullResyncRequiredError,
    FullSnapshotRecoveryError,
    MissingCommittedCheckpointError,
    ReliableIncrementalSync,
)
from gmail_ingestion.auth import CredentialStoreError, GmailAuthError, build_provider
from gmail_ingestion.config import default_credential_dir
from gmail_ingestion.persistence import (
    CheckpointStoreError,
    LocalCheckpointStore,
    LocalHistoryReceiptStore,
)

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
        typer.Option(
            "--checkpoint-root",
            help="Persistent checkpoints and History event receipts.",
        ),
    ] = _DEFAULT_CHECKPOINT_ROOT,
    max_results: Annotated[
        int,
        typer.Option("--max-results", min=1, max=500, help="History records per Gmail page."),
    ] = 500,
    auto_full_resync: Annotated[
        bool,
        typer.Option(
            "--auto-full-resync/--no-auto-full-resync",
            help="Recover a missing/expired cursor with Full Snapshot then History catch-up.",
        ),
    ] = True,
) -> None:
    """Apply Gmail History with replay safety and automatic snapshot recovery."""
    try:
        parsed_run_id = UUID(run_id) if run_id is not None else None
        provider = build_provider(Path("credentials.json"), credential_dir)
        result = ReliableIncrementalSync(
            provider,
            LocalArtifactStore(artifact_root),
            LocalCheckpointStore(checkpoint_root),
            receipt_store=LocalHistoryReceiptStore(checkpoint_root),
        ).run(
            account_id,
            run_id=parsed_run_id,
            max_results=max_results,
            auto_full_resync=auto_full_resync,
        )
    except (
        GmailAuthError,
        GmailApiError,
        CredentialStoreError,
        CheckpointStoreError,
        MissingCommittedCheckpointError,
        FullResyncRequiredError,
        FullSnapshotRecoveryError,
        OSError,
        ValueError,
    ) as exc:
        typer.echo(f"Incremental sync failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    incremental = result.incremental
    typer.echo(f"run_id: {incremental.run_id}")
    typer.echo(f"account_id: {incremental.account_id}")
    typer.echo(f"input_checkpoint: {incremental.input_history_id}")
    typer.echo(f"committed_checkpoint: {incremental.output_history_id}")
    typer.echo(f"pages_scanned: {incremental.pages_scanned}")
    typer.echo(f"reused_pages: {incremental.reused_pages}")
    typer.echo(f"events_persisted: {incremental.events_persisted}")
    typer.echo(f"duplicate_events_ignored: {incremental.duplicate_events_ignored}")
    typer.echo(f"replayed_events: {incremental.replayed_events}")
    typer.echo(f"messages_hydrated: {incremental.messages_hydrated}")
    typer.echo(f"messages_reused: {incremental.messages_reused}")
    typer.echo(f"message_tombstones: {incremental.message_tombstones}")
    typer.echo(f"label_mutations: {incremental.label_mutations}")
    typer.echo(f"recovered_commit: {str(incremental.recovered).lower()}")
    typer.echo(
        "full_snapshot_recovery: "
        f"{str(result.recovered_with_full_snapshot).lower()}"
    )
    if result.full_snapshot_run_id is not None:
        typer.echo(f"full_snapshot_run_id: {result.full_snapshot_run_id}")
    if result.bootstrap_history_id is not None:
        typer.echo(f"bootstrap_history_id: {result.bootstrap_history_id}")
    typer.echo(f"manifest: {incremental.manifest_path}")
