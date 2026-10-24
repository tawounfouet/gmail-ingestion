"""CLI command for interactive Gmail authorization."""

from pathlib import Path

import typer

from gmail_ingestion.api import GmailApiError, GmailProfileError
from gmail_ingestion.application import AuthenticateMailbox
from gmail_ingestion.auth import CredentialStoreError, GmailAuthError, build_provider
from gmail_ingestion.config import default_credential_dir

from .render import render_account


def auth_command(
    client_secrets: Path = typer.Option(
        Path("credentials.json"),
        "--client-secrets",
        help="Google OAuth Desktop client JSON file.",
    ),
    credential_dir: Path = typer.Option(
        default_credential_dir(),
        "--credential-dir",
        help="Private local credential store.",
    ),
    no_browser: bool = typer.Option(
        False,
        "--no-browser",
        help="Do not automatically open the authorization URL in a browser.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Render the Gmail profile as JSON."),
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
