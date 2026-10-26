from typer.main import get_command
from typer.testing import CliRunner

import gmail_ingestion
from gmail_ingestion.cli import app

runner = CliRunner()


def test_package_version() -> None:
    assert gmail_ingestion.__version__


def test_cli_help_exposes_gmail_commands() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "auth" in result.stdout
    assert "profile" in result.stdout
    assert "project" in result.stdout
    assert "labels" in result.stdout
    assert "snapshot" in result.stdout
    assert "range" in result.stdout
    assert "incremental" in result.stdout


def test_cli_version() -> None:
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == gmail_ingestion.__version__


def test_labels_help_renders() -> None:
    result = runner.invoke(app, ["labels", "--help"])
    assert result.exit_code == 0
    assert result.exception is None


def test_snapshot_help_and_attachment_options_are_registered() -> None:
    result = runner.invoke(app, ["snapshot", "--help"])
    assert result.exit_code == 0
    assert result.exception is None

    command = get_command(app)
    snapshot = command.commands["snapshot"]
    parameter_names = {parameter.name for parameter in snapshot.params}
    assert "download_attachments" in parameter_names
    assert "max_attachment_bytes" in parameter_names
    assert "query" in parameter_names
    assert "label_ids" in parameter_names
    assert "after" in parameter_names
    assert "before" in parameter_names
    assert "last_days" in parameter_names


def test_range_help_and_date_options_are_registered() -> None:
    result = runner.invoke(app, ["range", "--help"])
    assert result.exit_code == 0
    assert result.exception is None

    command = get_command(app)
    range_command = command.commands["range"]
    parameter_names = {parameter.name for parameter in range_command.params}
    assert "after" in parameter_names
    assert "before" in parameter_names
    assert "last_days" in parameter_names
    assert "query" in parameter_names
    assert "label_ids" in parameter_names


def test_incremental_help_and_checkpoint_options_are_registered() -> None:
    result = runner.invoke(app, ["incremental", "--help"])
    assert result.exit_code == 0
    assert result.exception is None

    command = get_command(app)
    incremental = command.commands["incremental"]
    parameter_names = {parameter.name for parameter in incremental.params}
    assert "checkpoint_root" in parameter_names
    assert "artifact_root" in parameter_names
    assert "max_results" in parameter_names
    assert "auto_full_resync" in parameter_names



def test_project_help_and_projection_options_are_registered() -> None:
    result = runner.invoke(app, ["project", "--help"])
    assert result.exit_code == 0
    assert result.exception is None

    command = get_command(app)
    project = command.commands["project"]
    parameter_names = {parameter.name for parameter in project.params}
    assert "account_id" in parameter_names
    assert "baseline_run_id" in parameter_names
    assert "delta_run_ids" in parameter_names
    assert "baseline_dataset_version" in parameter_names
    assert "artifact_root" in parameter_names
    assert "projection_root" in parameter_names
    assert "candidate_only" in parameter_names
