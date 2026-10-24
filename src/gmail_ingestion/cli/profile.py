"""CLI command for Gmail profile retrieval."""

from pathlib import Path
from typing import Annotated

import typer

from gmail_ingestion.api import GmailApiError, GmailProfileError
from gmail_ingestion.application import GetMailboxProfile
from gmail_ingestion.auth import CredentialStoreError, GmailAuthError, build_provider
from gmail_ingestion.config import default_credential_dir

from .render import render_account

_DEFAULT_CREDENTIAL_DIR = default_credential_dir()


def profile_command(
    account_id: Annotated[
        str | None,
        typer.Option(
            "--account-id",
            help="Opaque local account ID. Defaults to the active account.",
        ),
    ] = None,
    credential_dir: Annotated[
        Path,
        typer.Option(
            "--credential-dir",
            help="Private local credential store.",
        ),
    ] = _DEFAULT_CREDENTIAL_DIR,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Render the Gmail profile as JSON."),
    ] = False,
) -> None:
    """Fetch users.getProfile using stored reusable credentials."""
    try:
        provider = build_provider(Path("credentials.json"), credential_dir)
        account = GetMailboxProfile(provider).run(account_id)
    except (GmailAuthError, GmailApiError, GmailProfileError, CredentialStoreError, OSError) as exc:
        typer.echo(f"Profile retrieval failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    render_account(account, as_json=as_json)
