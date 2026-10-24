"""CLI command for interactive Gmail authorization."""

from pathlib import Path
from typing import Annotated

import typer

from gmail_ingestion.api import GmailApiError, GmailProfileError
from gmail_ingestion.application import AuthenticateMailbox
from gmail_ingestion.auth import CredentialStoreError, GmailAuthError, build_provider
from gmail_ingestion.config import default_credential_dir

from .render import render_account

_DEFAULT_CLIENT_SECRETS = Path("credentials.json")
_DEFAULT_CREDENTIAL_DIR = default_credential_dir()


def auth_command(
    client_secrets: Annotated[
        Path,
        typer.Option(
            "--client-secrets",
            help="Google OAuth Desktop client JSON file.",
        ),
    ] = _DEFAULT_CLIENT_SECRETS,
    credential_dir: Annotated[
        Path,
        typer.Option(
            "--credential-dir",
            help="Private local credential store.",
        ),
    ] = _DEFAULT_CREDENTIAL_DIR,
    no_browser: Annotated[
        bool,
        typer.Option(
            "--no-browser",
            help="Do not automatically open the authorization URL in a browser.",
        ),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Render the Gmail profile as JSON."),
    ] = False,
) -> None:
    """Authorize a Gmail mailbox with the read-only OAuth scope."""
    try:
        provider = build_provider(
            client_secrets,
            credential_dir,
            open_browser=not no_browser,
        )
        account = AuthenticateMailbox(provider).run()
    except (
        GmailAuthError,
        GmailApiError,
        GmailProfileError,
        CredentialStoreError,
        OSError,
        ValueError,
    ) as exc:
        typer.echo(f"Authentication failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    render_account(account, as_json=as_json)
