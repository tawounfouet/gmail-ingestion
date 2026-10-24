"""Command-line entry point for Gmail Ingestion."""

import typer

from ._version import __version__

app = typer.Typer(
    name="gmail-ingest",
    help="Reliable, versioned Gmail ingestion powered by PyIngestKit.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Gmail Ingestion command-line interface."""


@app.command()
def version() -> None:
    """Print the installed Gmail Ingestion version."""
    typer.echo(__version__)
