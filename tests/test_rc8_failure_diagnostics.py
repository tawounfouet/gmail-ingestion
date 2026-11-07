"""Fail-closed human-readable diagnostics for RC8 materialization failures."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def module(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("materialize_rc8_current_candidate")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ("Insufficient available RAM for Current candidate", "AVAILABLE_RAM_BELOW_RESERVE"),
        ("Insufficient free disk for Current candidate", "DISK_RESERVE_EXHAUSTED"),
        ("Current recovery evidence identity mismatch", "RECOVERY_PROVENANCE_MISMATCH"),
        ("Recovery RAW content digest mismatch", "RECOVERY_RAW_DIGEST_MISMATCH"),
        (
            "Current in-scope added message has no proven original/current RAW",
            "REQUIRED_CURRENT_MESSAGE_RAW_MISSING",
        ),
        ("Incomplete candidate data already exists", "CANDIDATE_PARTIAL_OUTPUT_PRESENT"),
        ("RC8 deterministic replay digest differs", "REPLAY_DIGEST_MISMATCH"),
    ],
)
def test_known_reason_is_safe_and_stable(module: Any, error: str, expected: str) -> None:
    assert module.sanitized_materialization_reason(
        module.CandidateMaterializationError(error)
    ) == expected


def test_unclassified_and_other_exception_payload_are_not_exposed(module: Any) -> None:
    secret = "private-mail-id-and-full-path"
    assert module.sanitized_materialization_reason(
        module.CandidateMaterializationError(secret)
    ) == "UNCLASSIFIED_FAIL_CLOSED"
    assert module.sanitized_materialization_reason(
        RuntimeError(secret)
    ) == "UNCLASSIFIED_FAIL_CLOSED"


@pytest.mark.parametrize(
    ("exception", "expected"),
    [
        (
            "Insufficient available RAM for Current candidate",
            "reason_code=AVAILABLE_RAM_BELOW_RESERVE",
        ),
        (
            "Incomplete candidate data already exists",
            "reason_code=CANDIDATE_PARTIAL_OUTPUT_PRESENT",
        ),
    ],
)
def test_cli_prints_diagnostic_without_private_ids(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exception: str,
    expected: str,
) -> None:
    def failure(**_: Any) -> Any:
        raise module.CandidateMaterializationError(exception)

    monkeypatch.setattr(module, "build_current_candidate", failure)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "materialize_rc8_current_candidate.py",
            "--artifact-root", "/tmp/private-artifacts",
            "--projection-root", "/tmp/candidate",
            "--account-id", "acct_0123456789abcdef01234567",
            "--baseline-run-id", "00000000-0000-0000-0000-000000000001",
            "--delta-run-id", "00000000-0000-0000-0000-000000000002",
            "--source-inventory", "/tmp/private-source.json",
            "--recovery-run-id", "00000000-0000-0000-0000-000000000003",
        ],
    )
    assert module.main() == 2
    output = capsys.readouterr().out
    assert "RC8 MATERIALIZATION FAILED: exception_type=CandidateMaterializationError" in output
    assert expected in output
    assert "/tmp/private" not in output
    assert "acct_012" not in output


def test_cli_does_not_echo_unknown_dependency_exceptions(
    module: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def failure(**_: Any) -> Any:
        raise RuntimeError("secret.gmail@example.com: /home/private/message.json")

    monkeypatch.setattr(module, "build_current_candidate", failure)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "materialize_rc8_current_candidate.py",
            "--artifact-root", "/tmp/private-artifacts",
            "--projection-root", "/tmp/candidate",
            "--account-id", "acct_0123456789abcdef01234567",
            "--baseline-run-id", "00000000-0000-0000-0000-000000000001",
            "--delta-run-id", "00000000-0000-0000-0000-000000000002",
            "--source-inventory", "/tmp/private-source.json",
            "--recovery-run-id", "00000000-0000-0000-0000-000000000003",
        ],
    )
    assert module.main() == 2
    output = capsys.readouterr().out
    assert "reason_code=UNCLASSIFIED_FAIL_CLOSED" in output
    assert "secret.gmail" not in output
    assert "/home/private" not in output
