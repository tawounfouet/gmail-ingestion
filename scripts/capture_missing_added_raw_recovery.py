#!/usr/bin/env python3
"""Capture currently accessible missing Gmail messages as non-authoritative RAW evidence.

This deliberately DOES NOT repair historic History events, checkpoint state or Current State.
All writes occur in a separate gmail.recovery_evidence namespace. No message content,
message identifiers, email addresses or OAuth credentials are printed.
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from probe_missing_added_raw_live import unresolved_ids
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.api import GmailApiClient, GmailApiError, GmailMessageNotFoundError
from gmail_ingestion.auth import GmailAuthError, build_provider
from gmail_ingestion.config import default_credential_dir
from gmail_ingestion.persistence.raw_message_writer import RawMessageWriter

_RECOVERY_JOB_ID = "gmail.recovery_evidence"


class RecoveryEvidenceError(RuntimeError):
    """A safe, content-free recovery evidence failure."""


def capture_evidence(
    *,
    artifact_root: Path,
    account_id: str,
    baseline_run_id: UUID,
    delta_run_ids: tuple[UUID, ...],
    recovery_run_id: UUID,
    client: Any,
    max_messages: int = 100,
    max_message_bytes: int = 32 * 1024 * 1024,
    max_total_bytes: int = 512 * 1024 * 1024,
) -> dict[str, object]:
    """Fetch full representations into an isolated namespace with explicit size limits."""
    if max_messages < 1 or max_message_bytes < 1 or max_total_bytes < 1:
        raise RecoveryEvidenceError("Safety limits must be positive")
    ids, historical_count, target_history_id = unresolved_ids(
        artifact_root, account_id, baseline_run_id, delta_run_ids
    )
    if len(ids) > max_messages:
        raise RecoveryEvidenceError("Unresolved messages exceed configured request limit")
    store = LocalArtifactStore(artifact_root)
    writer = RawMessageWriter(store, job_id=_RECOVERY_JOB_ID)
    meta_path = store.run_root(_RECOVERY_JOB_ID, recovery_run_id) / "recovery_manifest.json"
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RecoveryEvidenceError("Invalid previous recovery manifest") from exc
        if (
            not isinstance(meta, dict)
            or meta.get("status") != "COMPLETE"
            or meta.get("account_id") != account_id
            or meta.get("baseline_run_id") != str(baseline_run_id)
            or meta.get("delta_run_ids") != [str(x) for x in delta_run_ids]
            or meta.get("historical_target_history_id") != target_history_id
            or meta.get("unique_ids_evaluated") != len(ids)
        ):
            raise RecoveryEvidenceError("Previous recovery manifest does not match input")
        captured_ids = meta.get("captured_ids")
        missing_ids = meta.get("not_found_ids")
        if (
            not isinstance(captured_ids, list)
            or not isinstance(missing_ids, list)
            or not all(isinstance(x, str) for x in captured_ids + missing_ids)
            or set(captured_ids).intersection(missing_ids)
            or set(captured_ids + missing_ids) != ids
            or not all(writer.exists(recovery_run_id, item) for item in captured_ids)
        ):
            raise RecoveryEvidenceError("Completed recovery manifest has missing or inconsistent RAW")
        return _public_summary(meta, reused=True)

    accessible = missing = reused_raw = calls = 0
    captured_ids: list[str] = []
    not_found_ids: list[str] = []
    observed_before_or_at_target = observed_after_target = unknown_history = 0
    total_bytes = 0
    for message_id in sorted(ids):
        stage = "read-existing"
        try:
            if writer.exists(recovery_run_id, message_id):
                payload = writer.read(recovery_run_id, message_id)
                reused_raw += 1
            else:
                stage = "fetch-live-full"
                try:
                    payload = client.get_message(message_id, format="full")
                except GmailMessageNotFoundError:
                    missing += 1
                    calls += 1
                    not_found_ids.append(message_id)
                    continue
                calls += 1
                if payload.get("id") != message_id:
                    raise RecoveryEvidenceError("Gmail full message identity mismatch")
                # Verify serialized payload size before ever writing sensitive data.
                stage = "enforce-size"
                raw_data = json.dumps(payload, ensure_ascii=False, default=str).encode()
                if len(raw_data) > max_message_bytes:
                    raise RecoveryEvidenceError("Full Gmail message exceeds per-message cap")
                if total_bytes + len(raw_data) > max_total_bytes:
                    raise RecoveryEvidenceError("Recovery exceeds cumulative byte cap")
                stage = "write-derived"
                writer.write(recovery_run_id, message_id, payload)
            accessible += 1
            captured_ids.append(message_id)
            # Current historyId is only an observation: it cannot prove prior label state.
            current_history = payload.get("historyId")
            if isinstance(current_history, str) and current_history.isdecimal():
                if int(current_history) > int(target_history_id):
                    observed_after_target += 1
                else:
                    observed_before_or_at_target += 1
            else:
                unknown_history += 1
            if stage == "read-existing":
                total_bytes += len(json.dumps(payload, ensure_ascii=False, default=str).encode())
            else:
                total_bytes += len(raw_data)
            if total_bytes > max_total_bytes:
                raise RecoveryEvidenceError("Recovery exceeds cumulative byte cap")
        except (OSError, ValueError, GmailApiError, RecoveryEvidenceError) as exc:
            raise RecoveryEvidenceError(
                f"Recovery interrupted (stage={stage}, "
                f"completed_requests={calls}, recovered={accessible}, "
                f"exception_type={type(exc).__name__})"
            ) from exc

    completed_at = datetime.now(UTC).isoformat()
    manifest: dict[str, object] = {
        "schema_version": 1,
        "status": "COMPLETE",
        "account_id": account_id,
        "baseline_run_id": str(baseline_run_id),
        "delta_run_ids": [str(x) for x in delta_run_ids],
        "recovery_run_id": str(recovery_run_id),
        "historical_target_history_id": target_history_id,
        "historical_unresolved_add_events": historical_count,
        "unique_ids_evaluated": len(ids),
        "captured_ids": captured_ids,
        "not_found_ids": not_found_ids,
        "captured_current_full": accessible,
        "not_found_404_now": missing,
        "existing_derived_raw_reused": reused_raw,
        "current_message_history_le_target": observed_before_or_at_target,
        "current_message_history_gt_target": observed_after_target,
        "current_message_history_unknown": unknown_history,
        "source_calls": calls,
        "bytes_serialized": total_bytes,
        "observed_at_utc": completed_at,
        "format": "full",
        "namespace": _RECOVERY_JOB_ID,
        "historical_event_mutations": 0,
        "history_checkpoint_mutations": 0,
        "projection_current_pointer_mutations": 0,
        "non_authoritative": True,
        "warning": (
            "This is a dated current Gmail recovery observation. It does not reconstruct "
            "historical RAW or prove message labels at the target History frontier."
        ),
    }
    # Publish the completion marker last, after every recoverable RAW artifact.
    try:
        store.write_json(_RECOVERY_JOB_ID, recovery_run_id, "recovery_manifest.json", manifest)
    except (OSError, ValueError) as exc:
        raise RecoveryEvidenceError("Could not publish completed recovery evidence") from exc
    return _public_summary(manifest, reused=False)


def _public_summary(meta: dict[str, Any], *, reused: bool) -> dict[str, object]:
    return {
        key: value for key, value in meta.items()
        if key not in {
            "account_id", "delta_run_ids", "baseline_run_id",
            "captured_ids", "not_found_ids",
        }
    } | {"manifest_reused": reused}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--baseline-run-id", required=True, type=UUID)
    parser.add_argument("--delta-run-id", required=True, action="append", type=UUID)
    parser.add_argument("--credential-dir", type=Path, default=default_credential_dir())
    parser.add_argument("--recovery-run-id", type=UUID, default=None)
    parser.add_argument("--max-messages", type=int, default=100)
    parser.add_argument("--max-message-mib", type=int, default=32)
    parser.add_argument("--max-total-mib", type=int, default=512)
    args = parser.parse_args()
    recovery_run_id = args.recovery_run_id or uuid4()
    print(f"recovery_run_id: {recovery_run_id}", flush=True)
    print("source_historical_runs_unchanged: true", flush=True)
    try:
        provider = build_provider(Path("credentials.json"), args.credential_dir)
        authenticated, credentials = provider.load(args.account_id)
        if authenticated != args.account_id:
            raise RecoveryEvidenceError("Authenticated account differs from requested account")
        client = GmailApiClient(credentials=credentials).messages
        result = capture_evidence(
            artifact_root=args.artifact_root,
            account_id=args.account_id,
            baseline_run_id=args.baseline_run_id,
            delta_run_ids=tuple(args.delta_run_id),
            recovery_run_id=recovery_run_id,
            client=client,
            max_messages=args.max_messages,
            max_message_bytes=args.max_message_mib * 1024 * 1024,
            max_total_bytes=args.max_total_mib * 1024 * 1024,
        )
    except (OSError, ValueError, RuntimeError, GmailAuthError) as exc:
        print(f"RECOVERY FAILED: exception_type={type(exc).__name__}")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
