#!/usr/bin/env python
"""Qualify Gmail Ingestion 0.4.0 incremental recovery against a real mailbox.

The connector stays read-only. Mailbox mutations needed to exercise Gmail History are performed
manually by the operator between the prepare and verify phases.
"""

from __future__ import annotations

import argparse
import json
import os
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion import __version__
from gmail_ingestion.application import IncrementalHistorySync, ReliableIncrementalSync
from gmail_ingestion.auth import build_provider
from gmail_ingestion.config import default_credential_dir
from gmail_ingestion.domain import GmailCheckpoint
from gmail_ingestion.persistence import (
    LocalCheckpointStore,
    LocalHistoryReceiptStore,
)

_ALLOWED_VERSIONS = {"0.4.0rc1", "0.4.0"}
_EVENT_TYPES = (
    "MESSAGE_ADDED",
    "MESSAGE_DELETED",
    "LABEL_ADDED",
    "LABEL_REMOVED",
)
_SECRET_KEYS = ("token", "refresh_token", "client_secret")
_SESSION_SCHEMA_VERSION = 1
_EVIDENCE_SCHEMA_VERSION = 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Qualify Gmail Ingestion 0.4.0 on a real Gmail mailbox without emitting "
            "message content or OAuth secrets."
        )
    )
    subparsers = parser.add_subparsers(dest="phase", required=True)

    prepare = subparsers.add_parser(
        "prepare",
        help=(
            "Run a complete Full Snapshot recovery + History catch-up in isolated local "
            "qualification storage."
        ),
    )
    _common_arguments(prepare)
    prepare.add_argument(
        "--full-mailbox",
        action="store_true",
        help=(
            "Required acknowledgement that the stable gate exports the complete mailbox "
            "into the isolated qualification artifact root."
        ),
    )
    prepare.add_argument(
        "--snapshot-run-id",
        type=UUID,
        help=(
            "Resume a previously started Full Snapshot using its exact run UUID. "
            "If omitted, prepare creates and persists a new resumable run UUID."
        ),
    )
    prepare.add_argument("--max-workers", type=int, default=4)
    prepare.add_argument("--max-results", type=int, default=500)

    verify = subparsers.add_parser(
        "verify",
        help=(
            "Consume real History changes created after prepare and prove same-run and "
            "cross-run replay behavior."
        ),
    )
    _common_arguments(verify)
    verify.add_argument("--max-results", type=int, default=500)
    verify.add_argument(
        "--require-event-type",
        action="append",
        choices=_EVENT_TYPES,
        help=(
            "Required real History event type. Repeat for multiple values. "
            "If omitted, all four stable event types are required."
        ),
    )
    verify.add_argument(
        "--output",
        type=Path,
        default=Path("qualification/v0.4.0-live-evidence.json"),
    )
    return parser


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--account-id")
    parser.add_argument("--credential-dir", type=Path, default=default_credential_dir())
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path(".gmail-ingestion-live-0.4.0"),
    )
    parser.add_argument(
        "--checkpoint-root",
        type=Path,
        default=Path(".gmail-ingestion-control-live-0.4.0"),
    )
    parser.add_argument(
        "--session",
        type=Path,
        default=Path("qualification/v0.4.0-live-session.json"),
    )


def _credential_secrets(payload: dict[str, Any]) -> tuple[bytes, ...]:
    values: list[bytes] = []
    for key in _SECRET_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            values.append(value.encode("utf-8"))
    return tuple(values)


def _tree_contains_secret(root: Path, secrets: tuple[bytes, ...]) -> bool:
    if not secrets or not root.exists():
        return False
    longest_secret = max(len(secret) for secret in secrets)
    overlap = max(0, longest_secret - 1)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            with path.open("rb") as handle:
                tail = b""
                while True:
                    chunk = handle.read(1024 * 1024)
                    if not chunk:
                        break
                    data = tail + chunk
                    if any(secret in data for secret in secrets):
                        return True
                    tail = data[-overlap:] if overlap else b""
        except OSError:
            continue
    return False


def _write_private_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        try:
            path.chmod(0o600)
        except OSError:
            pass
    finally:
        if temp.exists():
            temp.unlink()


def _read_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Unable to read qualification JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"Qualification JSON {path} must contain an object")
    return payload


def _event_type_counts(store: LocalArtifactStore, run_id: UUID) -> Counter[str]:
    directory = store.run_root("gmail.history", run_id) / "history_events"
    counts: Counter[str] = Counter()
    if not directory.exists():
        return counts
    for path in sorted(directory.glob("*.json")):
        payload = _read_object(path)
        event_type = payload.get("event_type")
        if isinstance(event_type, str):
            counts[event_type] += 1
    return counts


def _all_events_processed(store: LocalArtifactStore, run_id: UUID) -> bool:
    directory = store.run_root("gmail.history", run_id) / "history_events"
    if not directory.exists():
        return False
    paths = sorted(directory.glob("*.json"))
    if not paths:
        return False
    return all(
        _read_object(path).get("processing_status") == "PROCESSED"
        for path in paths
    )


def _ensure_version() -> None:
    if __version__ not in _ALLOWED_VERSIONS:
        raise SystemExit(
            "Incremental stable qualification requires gmail-ingestion "
            f"{sorted(_ALLOWED_VERSIONS)}, found {__version__}"
        )


def _prepare(args: argparse.Namespace) -> int:
    if not args.full_mailbox:
        raise SystemExit(
            "Stable qualification requires an explicit --full-mailbox acknowledgement. "
            "A partial snapshot is not a valid incremental baseline."
        )
    if not 1 <= args.max_results <= 500:
        raise SystemExit("--max-results must be between 1 and 500")
    if args.max_workers < 1:
        raise SystemExit("--max-workers must be >= 1")
    existing_session = _read_object(args.session) if args.session.exists() else None

    provider = build_provider(Path("credentials.json"), args.credential_dir)
    account_id, credential_payload = provider.store.load_credentials_info(args.account_id)
    secrets = _credential_secrets(credential_payload)
    store = LocalArtifactStore(args.artifact_root)
    checkpoints = LocalCheckpointStore(args.checkpoint_root)
    receipts = LocalHistoryReceiptStore(args.checkpoint_root)

    if existing_session is not None:
        if existing_session.get("schema_version") != _SESSION_SCHEMA_VERSION:
            raise SystemExit("Unsupported live qualification session schema")
        if existing_session.get("status") != "PREPARING":
            raise SystemExit(
                f"Qualification session already completed or is not resumable: {args.session}"
            )
        if existing_session.get("account_id") != account_id:
            raise SystemExit("Preparing session account does not match the selected Gmail account")
        if Path(str(existing_session.get("artifact_root"))) != args.artifact_root:
            raise SystemExit("Preparing session artifact root does not match --artifact-root")
        if Path(str(existing_session.get("checkpoint_root"))) != args.checkpoint_root:
            raise SystemExit("Preparing session checkpoint root does not match --checkpoint-root")
        raw_snapshot_run_id = existing_session.get("snapshot_run_id")
        try:
            snapshot_run_id = UUID(str(raw_snapshot_run_id))
        except (TypeError, ValueError) as exc:
            raise SystemExit("Preparing session contains an invalid snapshot_run_id") from exc
        if args.snapshot_run_id is not None and args.snapshot_run_id != snapshot_run_id:
            raise SystemExit(
                "--snapshot-run-id conflicts with the persisted PREPARING session run UUID"
            )
        raw_incremental_run_id = existing_session.get("incremental_run_id")
        if raw_incremental_run_id is None:
            incremental_run_id = uuid.uuid4()
            existing_session["incremental_run_id"] = str(incremental_run_id)
            _write_private_json(args.session, existing_session)
        else:
            try:
                incremental_run_id = UUID(str(raw_incremental_run_id))
            except (TypeError, ValueError) as exc:
                raise SystemExit(
                    "Preparing session contains an invalid incremental_run_id"
                ) from exc
        print(f"resume_snapshot_run_id: {snapshot_run_id}")
        print(f"incremental_run_id: {incremental_run_id}")
    else:
        snapshot_run_id = args.snapshot_run_id or uuid.uuid4()
        incremental_run_id = uuid.uuid4()
        _write_private_json(
            args.session,
            {
                "schema_version": _SESSION_SCHEMA_VERSION,
                "status": "PREPARING",
                "qualified_version": __version__,
                "prepared_at": datetime.now(UTC).isoformat(),
                "account_id": account_id,
                "full_mailbox": True,
                "artifact_root": str(args.artifact_root),
                "checkpoint_root": str(args.checkpoint_root),
                "snapshot_run_id": str(snapshot_run_id),
                "incremental_run_id": str(incremental_run_id),
            },
        )
        print(f"snapshot_run_id: {snapshot_run_id}")
        print(f"incremental_run_id: {incremental_run_id}")
        print(f"prepare_state: {args.session}")

    committed_before = checkpoints.load_committed(account_id)
    if committed_before is None:
        result = ReliableIncrementalSync(
            provider,
            store,
            checkpoints,
            receipt_store=receipts,
        ).run(
            account_id,
            run_id=incremental_run_id,
            max_results=args.max_results,
            auto_full_resync=True,
            snapshot_max_results=args.max_results,
            snapshot_max_workers=args.max_workers,
            snapshot_run_id=snapshot_run_id,
        )
        if not result.recovered_with_full_snapshot:
            raise SystemExit("Prepare phase did not exercise automatic Full Snapshot recovery")
        if result.full_snapshot_run_id is None or result.bootstrap_history_id is None:
            raise SystemExit(
                "Prepare phase did not return complete Full Snapshot bootstrap evidence"
            )
        if result.full_snapshot_run_id != snapshot_run_id:
            raise SystemExit(
                "Prepare phase returned a different Full Snapshot run than the resumable run UUID"
            )
        incremental = result.incremental
        bootstrap_history_id = result.bootstrap_history_id
    elif committed_before.run_id == snapshot_run_id:
        print(
            "resume_stage: snapshot completed and bootstrap checkpoint already committed; "
            "resuming History catch-up"
        )
        bootstrap_history_id = committed_before.history_id
        incremental = IncrementalHistorySync(
            provider,
            store,
            checkpoints,
            receipt_store=receipts,
        ).run(
            account_id,
            run_id=incremental_run_id,
            max_results=args.max_results,
        )
    elif committed_before.run_id == incremental_run_id:
        print(
            "resume_stage: History catch-up already committed; recovering incremental result"
        )
        incremental = IncrementalHistorySync(
            provider,
            store,
            checkpoints,
            receipt_store=receipts,
        ).run(
            account_id,
            run_id=incremental_run_id,
            max_results=args.max_results,
        )
        bootstrap_history_id = incremental.input_history_id
    else:
        raise SystemExit(
            "Qualification checkpoint root contains an unrelated committed checkpoint; "
            "use the original qualification roots or choose fresh isolated roots."
        )

    committed = checkpoints.load_committed(account_id)
    if committed is None:
        raise SystemExit("Prepare phase did not leave a committed incremental checkpoint")

    checks = {
        "full_snapshot_recovery_exercised": True,
        "bootstrap_history_id_present": bool(bootstrap_history_id),
        "catchup_committed": committed.history_id == incremental.output_history_id,
        "catchup_input_matches_bootstrap": (
            incremental.input_history_id == bootstrap_history_id
        ),
        "oauth_secrets_absent_from_artifacts": not _tree_contains_secret(
            args.artifact_root,
            secrets,
        ),
        "oauth_secrets_absent_from_control": not _tree_contains_secret(
            args.checkpoint_root,
            secrets,
        ),
    }
    if not all(checks.values()):
        raise SystemExit(f"Prepare qualification checks failed: {checks}")

    session = {
        "schema_version": _SESSION_SCHEMA_VERSION,
        "status": "PREPARED",
        "qualified_version": __version__,
        "prepared_at": datetime.now(UTC).isoformat(),
        "account_id": account_id,
        "full_mailbox": True,
        "artifact_root": str(args.artifact_root),
        "checkpoint_root": str(args.checkpoint_root),
        "full_snapshot_run_id": str(snapshot_run_id),
        "bootstrap_history_id": bootstrap_history_id,
        "prepare_incremental_run_id": str(incremental.run_id),
        "baseline_committed_history_id": incremental.output_history_id,
        "prepare_checks": checks,
    }
    _write_private_json(args.session, session)

    print("prepare_gate: PASS")
    print(f"session: {args.session}")
    print("Next, create these bounded Gmail changes manually before running verify:")
    print("  1. Send one disposable test message to yourself.")
    print("  2. Add a disposable label to that message.")
    print("  3. Remove the disposable label from that message.")
    print("  4. Permanently delete the disposable message (not only move it to Trash).")
    print("The connector remains gmail.readonly and will only observe those changes.")
    return 0


def _verify(args: argparse.Namespace) -> int:
    if not 1 <= args.max_results <= 500:
        raise SystemExit("--max-results must be between 1 and 500")

    session = _read_object(args.session)
    if session.get("schema_version") != _SESSION_SCHEMA_VERSION:
        raise SystemExit("Unsupported live qualification session schema")
    if session.get("status") == "PREPARING":
        raise SystemExit(
            "Prepare phase is incomplete; resume prepare before running verify"
        )
    if session.get("status") not in {None, "PREPARED"}:
        raise SystemExit("Qualification session is not in a verifiable state")
    if session.get("full_mailbox") is not True:
        raise SystemExit("Session is not stable-eligible because its baseline was not full-mailbox")

    provider = build_provider(Path("credentials.json"), args.credential_dir)
    account_id, credential_payload = provider.store.load_credentials_info(args.account_id)
    if session.get("account_id") != account_id:
        raise SystemExit("Qualification session account does not match the selected Gmail account")

    artifact_root = Path(str(session.get("artifact_root")))
    checkpoint_root = Path(str(session.get("checkpoint_root")))
    if artifact_root != args.artifact_root or checkpoint_root != args.checkpoint_root:
        raise SystemExit(
            "Qualification roots differ from the prepare session; "
            "use the same --artifact-root and --checkpoint-root."
        )

    secrets = _credential_secrets(credential_payload)
    store = LocalArtifactStore(artifact_root)
    checkpoints = LocalCheckpointStore(checkpoint_root)
    receipts = LocalHistoryReceiptStore(checkpoint_root)

    committed_before = checkpoints.load_committed(account_id)
    expected_baseline = session.get("baseline_committed_history_id")
    if committed_before is None or committed_before.history_id != expected_baseline:
        raise SystemExit(
            "Qualification checkpoint moved since prepare; start a fresh isolated session."
        )

    run_id = uuid.uuid4()
    first = IncrementalHistorySync(
        provider,
        store,
        checkpoints,
        receipt_store=receipts,
    ).run(
        account_id,
        run_id=run_id,
        max_results=args.max_results,
    )

    counts = _event_type_counts(store, run_id)
    required = tuple(args.require_event_type or _EVENT_TYPES)
    required_present = all(counts[event_type] > 0 for event_type in required)

    same_run = IncrementalHistorySync(
        provider,
        store,
        checkpoints,
        receipt_store=receipts,
    ).run(
        account_id,
        run_id=run_id,
        max_results=args.max_results,
    )

    replay_root = checkpoint_root / f"replay-{run_id}"
    replay_checkpoints = LocalCheckpointStore(replay_root)
    replay_seed_run = uuid.uuid4()
    replay_seed = GmailCheckpoint.candidate(
        account_id=account_id,
        history_id=first.input_history_id,
        run_id=replay_seed_run,
        dataset_version=f"live-replay-seed-{replay_seed_run}",
    )
    replay_checkpoints.save_candidate(replay_seed)
    replay_checkpoints.commit(replay_seed, require_no_committed=True)

    replay_run_id = uuid.uuid4()
    cross_run = IncrementalHistorySync(
        provider,
        store,
        replay_checkpoints,
        receipt_store=receipts,
    ).run(
        account_id,
        run_id=replay_run_id,
        max_results=args.max_results,
    )

    checks = {
        "real_history_events_observed": first.events_persisted > 0,
        "required_event_types_present": required_present,
        "events_all_processed": _all_events_processed(store, run_id),
        "checkpoint_advanced": first.output_history_id != first.input_history_id,
        "same_run_recovered_without_reduction": same_run.recovered,
        "same_run_output_stable": same_run.output_history_id == first.output_history_id,
        "cross_run_receipts_replayed": cross_run.replayed_events >= first.events_persisted,
        "cross_run_no_message_rehydration": cross_run.messages_hydrated == 0,
        "cross_run_output_stable": cross_run.output_history_id == first.output_history_id,
        "oauth_secrets_absent_from_artifacts": not _tree_contains_secret(
            artifact_root,
            secrets,
        ),
        "oauth_secrets_absent_from_control": not _tree_contains_secret(
            checkpoint_root,
            secrets,
        ),
    }
    passed = all(checks.values())

    evidence = {
        "schema_version": _EVIDENCE_SCHEMA_VERSION,
        "qualified_version": __version__,
        "qualified_at": datetime.now(UTC).isoformat(),
        "account_id": account_id,
        "live_gate_passed": passed,
        "stable_eligible": set(required) == set(_EVENT_TYPES),
        "required_event_types": list(required),
        "observed_event_types": dict(sorted(counts.items())),
        "prepare": {
            "full_snapshot_run_id": session.get("full_snapshot_run_id"),
            "bootstrap_history_id_present": bool(session.get("bootstrap_history_id")),
            "prepare_incremental_run_id": session.get("prepare_incremental_run_id"),
            "prepare_checks": session.get("prepare_checks"),
        },
        "incremental": {
            "run_id": str(first.run_id),
            "input_history_id": first.input_history_id,
            "output_history_id": first.output_history_id,
            "pages_scanned": first.pages_scanned,
            "events_persisted": first.events_persisted,
            "replayed_events": first.replayed_events,
            "messages_hydrated": first.messages_hydrated,
            "message_tombstones": first.message_tombstones,
            "label_mutations": first.label_mutations,
        },
        "same_run_replay": {
            "recovered": same_run.recovered,
            "output_history_id": same_run.output_history_id,
        },
        "cross_run_replay": {
            "run_id": str(cross_run.run_id),
            "events_persisted": cross_run.events_persisted,
            "replayed_events": cross_run.replayed_events,
            "messages_hydrated": cross_run.messages_hydrated,
            "output_history_id": cross_run.output_history_id,
        },
        "checks": checks,
    }
    _write_private_json(args.output, evidence)

    print(f"live_gate: {'PASS' if passed else 'FAIL'}")
    print(f"stable_eligible: {str(evidence['stable_eligible']).lower()}")
    print(f"evidence: {args.output}")
    return 0 if passed and evidence["stable_eligible"] else 1


def main() -> int:
    _ensure_version()
    args = _parser().parse_args()
    if args.phase == "prepare":
        return _prepare(args)
    return _verify(args)


if __name__ == "__main__":
    raise SystemExit(main())
