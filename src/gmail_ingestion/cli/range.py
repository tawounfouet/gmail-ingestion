"""CLI command for Gmail date-range snapshots."""

from pathlib import Path
from typing import Annotated

import typer

from gmail_ingestion.config import default_credential_dir

from .snapshot import snapshot_command

_DEFAULT_CREDENTIAL_DIR = default_credential_dir()
_DEFAULT_ARTIFACT_ROOT = Path(".gmail-ingestion")


def range_command(
    account_id: Annotated[
        str | None,
        typer.Option("--account-id", help="Opaque account ID. Defaults to the active account."),
    ] = None,
    run_id: Annotated[
        str | None,
        typer.Option("--run-id", help="Existing run UUID to resume, or omit for a new run."),
    ] = None,
    credential_dir: Annotated[
        Path,
        typer.Option("--credential-dir", help="Private local credential store."),
    ] = _DEFAULT_CREDENTIAL_DIR,
    artifact_root: Annotated[
        Path,
        typer.Option("--artifact-root", help="PyIngestKit run-artifact root."),
    ] = _DEFAULT_ARTIFACT_ROOT,
    after: Annotated[
        str | None,
        typer.Option("--after", help="Gmail lower date bound as YYYY-MM-DD."),
    ] = None,
    before: Annotated[
        str | None,
        typer.Option("--before", help="Gmail upper date bound as YYYY-MM-DD."),
    ] = None,
    last_days: Annotated[
        int | None,
        typer.Option("--last-days", min=1, help="Freeze a relative lower bound at run start."),
    ] = None,
    query: Annotated[
        str | None,
        typer.Option("--query", help="Optional Gmail-native query combined with the date range."),
    ] = None,
    label_ids: Annotated[
        list[str] | None,
        typer.Option("--label-id", help="Optional Gmail label ID. Repeat for multiple labels."),
    ] = None,
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
    """Create/resume a Gmail snapshot constrained by an explicit date range."""
    if after is None and before is None and last_days is None:
        raise typer.BadParameter("range requires --after, --before, or --last-days")

    snapshot_command(
        account_id=account_id,
        run_id=run_id,
        credential_dir=credential_dir,
        artifact_root=artifact_root,
        include_spam_trash=include_spam_trash,
        query=query,
        label_ids=label_ids,
        after=after,
        before=before,
        last_days=last_days,
        max_results=max_results,
        max_workers=max_workers,
        max_attempts=max_attempts,
        download_attachments=download_attachments,
        max_attachment_bytes=max_attachment_bytes,
    )
