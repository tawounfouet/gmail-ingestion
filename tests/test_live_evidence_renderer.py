import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path("scripts/render_live_incremental_evidence.py")


def _payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "qualified_version": "0.4.0rc1",
        "qualified_at": "2026-10-25T15:30:00+00:00",
        "live_gate_passed": True,
        "stable_eligible": True,
        "required_event_types": [
            "LABEL_REMOVED",
            "MESSAGE_ADDED",
            "MESSAGE_DELETED",
            "LABEL_ADDED",
        ],
        "observed_event_types": {
            "MESSAGE_ADDED": 1,
            "MESSAGE_DELETED": 1,
            "LABEL_ADDED": 2,
            "LABEL_REMOVED": 2,
        },
        "checks": {
            "real_history_events_observed": True,
            "required_event_types_present": True,
            "events_all_processed": True,
            "checkpoint_advanced": True,
            "same_run_recovered_without_reduction": True,
            "same_run_output_stable": True,
            "cross_run_receipts_replayed": True,
            "cross_run_no_message_rehydration": True,
            "cross_run_output_stable": True,
            "oauth_secrets_absent_from_artifacts": True,
            "oauth_secrets_absent_from_control": True,
        },
    }


def test_renderer_accepts_complete_stable_evidence_independent_of_event_order(
    tmp_path: Path,
) -> None:
    source = tmp_path / "evidence.json"
    output = tmp_path / "evidence.md"
    source.write_text(json.dumps(_payload()), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(source),
            "--output",
            str(output),
            "--candidate-commit",
            "abc123",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    rendered = output.read_text(encoding="utf-8")
    assert "**PASS**" in rendered
    assert "candidate commit: abc123" in rendered
    assert "MESSAGE_DELETED" in rendered
    assert "account_id" not in rendered
    assert "input_history_id" not in rendered


def test_renderer_rejects_incomplete_event_coverage(tmp_path: Path) -> None:
    payload = _payload()
    observed_raw = payload["observed_event_types"]
    assert isinstance(observed_raw, dict)
    observed = dict(observed_raw)
    observed["MESSAGE_DELETED"] = 0
    payload["observed_event_types"] = observed

    source = tmp_path / "evidence.json"
    output = tmp_path / "evidence.md"
    source.write_text(json.dumps(payload), encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--input",
            str(source),
            "--output",
            str(output),
            "--candidate-commit",
            "abc123",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode != 0
    assert "MESSAGE_DELETED" in completed.stderr
    assert not output.exists()
