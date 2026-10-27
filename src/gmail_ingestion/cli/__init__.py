"""Command-line entry point for Gmail Ingestion."""

import typer

from gmail_ingestion._version import __version__

from .auth import auth_command
from .bootstrap_baseline import bootstrap_baseline_command
from .incremental import incremental_command
from .labels import labels_command
from .profile import profile_command
from .project import project_command
from .range import range_command
from .snapshot import snapshot_command

app = typer.Typer(
    name="gmail-ingest",
    help="Reliable, versioned Gmail ingestion powered by PyIngestKit.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Gmail Ingestion command-line interface."""


@app.command("version")
def version_command() -> None:
    """Print the installed Gmail Ingestion version."""
    typer.echo(__version__)


app.command("auth")(auth_command)
app.command("bootstrap-baseline")(bootstrap_baseline_command)
app.command("profile")(profile_command)
app.command("project")(project_command)
app.command("labels")(labels_command)
app.command("snapshot")(snapshot_command)
app.command("range")(range_command)
app.command("incremental")(incremental_command)

__all__ = ["app"]
