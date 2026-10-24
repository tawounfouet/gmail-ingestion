from typer.testing import CliRunner

import gmail_ingestion
from gmail_ingestion.cli import app

runner = CliRunner()


def test_package_version() -> None:
    assert gmail_ingestion.__version__ == "0.1.0b2"


def test_cli_help_exposes_gmail_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "auth" in result.stdout
    assert "profile" in result.stdout
    assert "labels" in result.stdout
    assert "snapshot" in result.stdout


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0b2"


def test_labels_help_renders() -> None:
    result = runner.invoke(app, ["labels", "--help"])
    assert result.exit_code == 0
    assert result.exception is None


def test_snapshot_help_renders() -> None:
    result = runner.invoke(app, ["snapshot", "--help"])
    assert result.exit_code == 0
    assert result.exception is None
    assert "--download-attachments" in result.stdout
    assert "--max-attachment-bytes" in result.stdout
