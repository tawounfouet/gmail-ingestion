import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path("scripts/render_live_projection_evidence.py")


def _payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "qualified_version": "0.5.0rc1",
        "qualified_at": "2026-10-26T22:50:00+00:00",
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
            "LABEL_ADDED": 1,
            "LABEL_REMOVED": 1,
        },
        "projection": {
            "projection_id": "proj_secret",
            "logical_digest": "abc",
            "rebuild_digest": "abc",
            "incremental_batch_count": 1,
            "rebuild_batch_count": 2,
            "promoted": True,
            "replay_already_current": True,
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
            "projection_promoted": True,
            "projection_target_matches_checkpoint": True,
            "incremental_equals_full_rebuild": True,
            "projection_replay_already_current": True,
            "projection_replay_digest_stable": True,
            "current_pointer_matches_target": True,
            "current_pointer_present": True,
            "deleted_messages_absent": True,
            "add_delete_race_converged": True,
            "add_remove_label_race_converged": True,
            "oauth_secrets_absent_from_artifacts": True,
            "oauth_secrets_absent_from_control": True,
            "oauth_secrets_absent_from_projection": True,
        },
    }


def test_projection_renderer_accepts_complete_stable_evidence(tmp_path: Path) -> None:
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
    assert "logical digest:      REDACTED / MATCHED" in rendered
    assert "proj_secret" not in rendered
    assert "account_id" not in rendered
    assert "input_history_id" not in rendered


def test_projection_renderer_rejects_digest_divergence(tmp_path: Path) -> None:
    payload = _payload()
    projection_raw = payload["projection"]
    assert isinstance(projection_raw, dict)
    projection = dict(projection_raw)
    projection["rebuild_digest"] = "different"
    payload["projection"] = projection

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
    assert "digest" in completed.stderr.lower()
    assert not output.exists()


def test_projection_renderer_rejects_incomplete_event_coverage(tmp_path: Path) -> None:
    payload = _payload()
    observed_raw = payload["observed_event_types"]
    assert isinstance(observed_raw, dict)
    observed = dict(observed_raw)
    observed["LABEL_REMOVED"] = 0
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
    assert "LABEL_REMOVED" in completed.stderr
    assert not output.exists()
