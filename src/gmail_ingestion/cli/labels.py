"""CLI command for the Gmail labels dataset."""

from pathlib import Path
from typing import Annotated

import typer
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.api import GmailApiError, GmailProfileError
from gmail_ingestion.application import IngestLabels
from gmail_ingestion.auth import CredentialStoreError, GmailAuthError, build_provider
from gmail_ingestion.config import default_credential_dir
from gmail_ingestion.normalization import GmailLabelNormalizationError

_DEFAULT_CREDENTIAL_DIR = default_credential_dir()
_DEFAULT_ARTIFACT_ROOT = Path(".gmail-ingestion")


def labels_command(
    account_id: Annotated[
        str | None,
        typer.Option("--account-id", help="Opaque account ID. Defaults to the active account."),
    ] = None,
    credential_dir: Annotated[
        Path,
        typer.Option("--credential-dir", help="Private local credential store."),
    ] = _DEFAULT_CREDENTIAL_DIR,
    artifact_root: Annotated[
        Path,
        typer.Option("--artifact-root", help="PyIngestKit run-artifact root."),
    ] = _DEFAULT_ARTIFACT_ROOT,
    details: Annotated[
        bool,
        typer.Option(
            "--details/--no-details",
            help="Enrich list results with users.labels.get details.",
        ),
    ] = True,
) -> None:
    """Ingest Gmail labels into immutable RAW JSON and normalized Parquet."""
    try:
        provider = build_provider(Path("credentials.json"), credential_dir)
        result = IngestLabels(provider, LocalArtifactStore(artifact_root)).run(
            account_id,
            include_details=details,
        )
    except (
        GmailAuthError,
        GmailApiError,
        GmailProfileError,
        GmailLabelNormalizationError,
        CredentialStoreError,
        OSError,
        ValueError,
    ) as exc:
        typer.echo(f"Labels ingestion failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"run_id: {result.run_id}")
    typer.echo(f"account_id: {result.account_id}")
    typer.echo(f"labels: {result.label_count}")
    typer.echo(f"raw_artifacts: {result.raw_artifact_count}")
    typer.echo(f"parquet: {result.parquet_path}")
    typer.echo(f"schema: {result.schema_path}")
