"""Safe CLI rendering helpers."""

from __future__ import annotations

import json
from dataclasses import asdict

import typer

from gmail_ingestion.domain import GmailAccount


def render_account(account: GmailAccount, *, as_json: bool) -> None:
    """Render profile metadata; OAuth credentials are never part of the domain object."""
    if as_json:
        typer.echo(json.dumps(asdict(account), indent=2, sort_keys=True))
        return
    typer.echo(f"account_id: {account.account_id}")
    typer.echo(f"email_address: {account.email_address}")
    typer.echo(f"messages_total: {account.messages_total}")
    typer.echo(f"threads_total: {account.threads_total}")
    typer.echo(f"history_id: {account.history_id}")
