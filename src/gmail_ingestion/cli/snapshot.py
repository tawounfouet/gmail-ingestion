"""CLI command for a resumable full Gmail message snapshot."""

from pathlib import Path
from typing import Annotated
from uuid import UUID

import typer
from pyingestkit.artifacts.filesystem import LocalArtifactStore
from pyingestkit.core.exceptions import StorageError

from gmail_ingestion.api import GmailApiError, GmailProfileError
from gmail_ingestion.application import FullMessageSnapshot
from gmail_ingestion.auth import CredentialStoreError, GmailAuthError, build_provider
from gmail_ingestion.config import default_credential_dir
from gmail_ingestion.control import MessageDiscoveryError

_DEFAULT_CREDENTIAL_DIR = default_credential_dir()
_DEFAULT_ARTIFACT_ROOT = Path(".gmail-ingestion")


def snapshot_command(
    account_id: Annotated[
        str | None,
        typer.Option("--account-id", help="Opaque account ID. Defaults to the active account."),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Existing run UUID to resume, or omit for a new snapshot."),
    ] = None,
    credential_dir: Annotated[
        Path,
        typer.Option("--credential-dir", help="Private local credential store."),
    ] = _DEFAULT_CREDENTIAL_DIR,
    artifact_root: Annotated[
        Path,
        typer.Option("--artifact-root", help="PyIngestKit run-artifact root."),
    ] = _DEFAULT_ARTIFACT_ROOT,
    include_spam_trash: Annotated[
        bool,
        typer.Option(
            "--include-spam-trash/--exclude-spam-trash",
            help="Include Gmail Spam and Trash during discovery.",
        ),
    ] = False,
    max_results: Annotated[
        int,
        typer.Option("--max-results", min=1, max=500, help="Messages per discovery page."),
    ] = 500,
    max_workers: Annotated[
        int,
        typer.Option("--max-workers", min=1, max=32, help="Bounded hydration workers."),
    ] = 4,
    max_attempts: Annotated[
        int,
        typer.Option("--max-attempts", min=1, help="Maximum GET attempts per message."),
    ] = 5,
) -> None:
    """Discover every message, hydrate format=full, persist immutable RAW and resume safely."""
    try:
        parsed_run_id = UUID(run_id) if run_id is not None else None
        provider = build_provider(Path("credentials.json"), credential_dir)
        result = FullMessageSnapshot(
            provider,
            LocalArtifactStore(artifact_root),
        ).run(
            account_id,
            run_id=parsed_run_id,
            include_spam_trash=include_spam_trash,
            max_results=max_results,
            max_workers=max_workers,
            max_attempts=max_attempts,
        )
    except (
        GmailAuthError,
        GmailApiError,
        GmailProfileError,
        CredentialStoreError,
        MessageDiscoveryError,
        StorageError,
        OSError,
        ValueError,
    ) as exc:
        typer.echo(f"Snapshot failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"run_id: {result.run_id}")
    typer.echo(f"account_id: {result.account_id}")
    typer.echo(f"history_start: {result.history_start or ''}")
    typer.echo(f"pages_scanned: {result.pages_scanned}")
    typer.echo(f"messages_discovered: {result.discovered_count}")
    typer.echo(f"messages_fetched: {result.fetched_count}")
    typer.echo(f"messages_failed: {result.failed_count}")
    typer.echo(f"raw_messages: {result.raw_message_count}")
    typer.echo(f"resumed: {str(result.resumed).lower()}")

    if result.failed_count:
        raise typer.Exit(code=2)
