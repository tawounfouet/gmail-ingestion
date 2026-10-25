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
    query: Annotated[
        str | None,
        typer.Option(
            "--query",
            help="Gmail-native search expression forwarded to users.messages.list.",
        ),
    ] = None,
    label_ids: Annotated[
        list[str] | None,
        typer.Option(
            "--label-id",
            help="Gmail label ID required during discovery. Repeat for multiple labels.",
        ),
    ] = None,
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
        typer.Option("--max-attempts", min=1, help="Maximum GET attempts per resource."),
    ] = 5,
    download_attachments: Annotated[
        bool,
        typer.Option(
            "--download-attachments/--metadata-only",
            help="Persist attachment binaries. Metadata-only is the safe default.",
        ),
    ] = False,
    max_attachment_bytes: Annotated[
        int | None,
        typer.Option(
            "--max-attachment-bytes",
            min=1,
            help="Skip binary persistence above this size. Unlimited when omitted.",
        ),
    ] = None,
) -> None:
    """Create/resume a full or explicitly filtered Gmail snapshot."""
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
            query=query,
            label_ids=tuple(label_ids or ()),
            max_results=max_results,
            max_workers=max_workers,
            max_attempts=max_attempts,
            download_attachments=download_attachments,
            max_attachment_bytes=max_attachment_bytes,
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
    typer.echo(f"mime_messages_processed: {result.mime_messages_processed}")
    typer.echo(f"mime_messages_failed: {result.mime_messages_failed}")
    typer.echo(f"mime_parts: {result.mime_parts}")
    typer.echo(f"attachments_detected: {result.attachments_detected}")
    typer.echo(f"attachments_fetched: {result.attachments_fetched}")
    typer.echo(f"attachments_failed: {result.attachments_failed}")
    typer.echo(f"attachments_skipped: {result.attachments_skipped}")
    typer.echo(f"bytes_downloaded: {result.bytes_downloaded}")
    typer.echo(f"manifest_status: {result.manifest_status}")
    typer.echo(f"manifest: {result.manifest_path}")
    typer.echo(f"resumed: {str(result.resumed).lower()}")

    if result.manifest_status != "COMPLETED":
        raise typer.Exit(code=2)
