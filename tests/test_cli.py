from typer.testing import CliRunner

import gmail_ingestion
from gmail_ingestion.cli import app

runner = CliRunner()


def test_package_version() -> None:
    assert gmail_ingestion.__version__ == "0.1.0a1"


def test_cli_help_exposes_auth_and_profile() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "auth" in result.stdout
    assert "profile" in result.stdout


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == "0.1.0a1"
