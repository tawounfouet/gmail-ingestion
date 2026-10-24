"""CLI command for Gmail profile retrieval."""

from pathlib import Path

import typer

from gmail_ingestion.api import GmailApiError, GmailProfileError
from gmail_ingestion.application import GetMailboxProfile
from gmail_ingestion.auth import CredentialStoreError, GmailAuthError, build_provider
from gmail_ingestion.config import default_credential_dir

from .render import render_account


def profile_command(
    account_id: str | None = typer.Option(
        None,
        "--account-id",
        help="Opaque local account ID. Defaults to the active account.",
    ),
    credential_dir: Path = typer.Option(
        default_credential_dir(),
        "--credential-dir",
        help="Private local credential store.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Render the Gmail profile as JSON."),
) -> None:
    """Fetch users.getProfile using stored reusable credentials."""
    try:
        provider = build_provider(Path("credentials.json"), credential_dir)
        account = GetMailboxProfile(provider).run(account_id)
    except (GmailAuthError, GmailApiError, GmailProfileError, CredentialStoreError, OSError) as exc:
        typer.echo(f"Profile retrieval failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    render_account(account, as_json=as_json)
