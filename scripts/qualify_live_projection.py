#!/usr/bin/env python
"""Qualify Gmail Ingestion 0.5.0 projection against a real Gmail mailbox.

The connector remains read-only. The operator performs bounded Gmail mutations manually
between prepare and verify. Evidence is redacted and never includes message content.
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
from gmail_ingestion.application import (
    IncrementalHistorySync,
    ProjectCurrentState,
    ReliableIncrementalSync,
)
from gmail_ingestion.auth import build_provider
from gmail_ingestion.config import default_credential_dir
from gmail_ingestion.domain import GmailCheckpoint, GmailHistoryEvent
from gmail_ingestion.persistence import (
    LocalCheckpointStore,
    LocalHistoryReceiptStore,
    LocalProjectionDatasetStore,
    LocalProjectionStore,
)
from gmail_ingestion.projection import CURRENT_PROJECTION_TABLES

_ALLOWED_VERSIONS = {"0.5.0rc1", "0.5.0"}
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
            "Qualify Gmail Ingestion 0.5.0 Current State materialization against a real "
            "mailbox without emitting Gmail content or OAuth secrets."
        )
    )
    subparsers = parser.add_subparsers(dest="phase", required=True)

    prepare = subparsers.add_parser(
        "prepare",
        help="Build a full normalized baseline, catch up History and publish initial Current State.",
    )
    _common_arguments(prepare)
    prepare.add_argument(
        "--full-mailbox",
        action="store_true",
        help="Required acknowledgement that the qualification exports the complete mailbox.",
    )
    prepare.add_argument("--snapshot-run-id", type=UUID)
    prepare.add_argument("--max-workers", type=int, default=4)
    prepare.add_argument("--max-results", type=int, default=500)

    verify = subparsers.add_parser(
        "verify",
        help="Consume bounded real History changes and prove projection/rebuild convergence.",
    )
    _common_arguments(verify)
    verify.add_argument("--max-results", type=int, default=500)
    verify.add_argument(
        "--require-event-type",
        action="append",
        choices=_EVENT_TYPES,
        help="Required event type. Repeat as needed. Stable eligibility requires all four.",
    )
    verify.add_argument(
        "--output",
        type=Path,
        default=Path("qualification/v0.5.0-live-evidence.json"),
    )
    return parser


def _common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--account-id")
    parser.add_argument("--credential-dir", type=Path, default=default_credential_dir())
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path(".gmail-ingestion-live-0.5.0"),
    )
    parser.add_argument(
        "--checkpoint-root",
        type=Path,
        default=Path(".gmail-ingestion-control-live-0.5.0"),
    )
    parser.add_argument(
        "--projection-root",
        type=Path,
        default=Path(".gmail-ingestion-projection-live-0.5.0"),
    )
    parser.add_argument(
        "--session",
        type=Path,
        default=Path("qualification/v0.5.0-live-session.json"),
    )


def _ensure_version() -> None:
    if __version__ not in _ALLOWED_VERSIONS:
        raise SystemExit(
            "Projection stable qualification requires gmail-ingestion "
            f"{sorted(_ALLOWED_VERSIONS)}, found {__version__}"
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
        temp.unlink(missing_ok=True)


def _read_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Unable to read qualification JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"Qualification JSON {path} must contain an object")
    return payload


def _history_events(
    store: LocalArtifactStore,
    run_id: UUID,
) -> tuple[GmailHistoryEvent, ...]:
    directory = store.run_root("gmail.history", run_id) / "history_events"
    if not directory.is_dir():
        return ()
    events: list[GmailHistoryEvent] = []
    for path in sorted(directory.glob("*.json")):
        payload = _read_object(path)
        events.append(GmailHistoryEvent.from_payload(payload))
    return tuple(events)


def _event_type_counts(events: tuple[GmailHistoryEvent, ...]) -> Counter[str]:
    return Counter(event.event_type.value for event in events)


def _all_events_processed(events: tuple[GmailHistoryEvent, ...]) -> bool:
    return bool(events) and all(
        event.processing_status.value == "PROCESSED"
        for event in events
    )


def _projection_mutation_checks(
    *,
    store: LocalProjectionStore,
    dataset_store: LocalProjectionDatasetStore,
    account_id: str,
    events: tuple[GmailHistoryEvent, ...],
) -> dict[str, bool]:
    current = store.load_current(account_id)
    if current is None:
        return {
            "current_pointer_present": False,
            "deleted_messages_absent": False,
            "add_delete_race_converged": False,
            "add_remove_label_race_converged": False,
        }
    rows = dataset_store.read_version(
        account_id,
        current.projection_id,
        table_names=CURRENT_PROJECTION_TABLES,
    )
    current_message_ids = {str(row["message_id"]) for row in rows["messages"]}
    current_labels = {
        (str(row["message_id"]), str(row["label_id"]))
        for row in rows["message_labels"]
    }

    deleted_ids = {
        event.message_id
        for event in events
        if event.event_type.value == "MESSAGE_DELETED"
    }
    added_ids = {
        event.message_id
        for event in events
        if event.event_type.value == "MESSAGE_ADDED"
    }
    label_added = {
        (event.message_id, str(event.label_id))
        for event in events
        if event.event_type.value == "LABEL_ADDED" and event.label_id is not None
    }
    label_removed = {
        (event.message_id, str(event.label_id))
        for event in events
        if event.event_type.value == "LABEL_REMOVED" and event.label_id is not None
    }
    add_delete = added_ids & deleted_ids
    add_remove_labels = label_added & label_removed

    return {
        "current_pointer_present": True,
        "deleted_messages_absent": all(
            message_id not in current_message_ids for message_id in deleted_ids
        ),
        "add_delete_race_converged": bool(add_delete)
        and all(message_id not in current_message_ids for message_id in add_delete),
        "add_remove_label_race_converged": bool(add_remove_labels)
        and all(relation not in current_labels for relation in add_remove_labels),
    }


def _prepare(args: argparse.Namespace) -> int:
    if not args.full_mailbox:
        raise SystemExit(
            "Stable projection qualification requires explicit --full-mailbox acknowledgement."
        )
    if not 1 <= args.max_results <= 500:
        raise SystemExit("--max-results must be between 1 and 500")
    if args.max_workers < 1:
        raise SystemExit("--max-workers must be >= 1")

    existing_session = _read_object(args.session) if args.session.exists() else None
    provider = build_provider(Path("credentials.json"), args.credential_dir)
    account_id, credential_payload = provider.store.load_credentials_info(args.account_id)
    secrets = _credential_secrets(credential_payload)

    artifacts = LocalArtifactStore(args.artifact_root)
    checkpoints = LocalCheckpointStore(args.checkpoint_root)
    receipts = LocalHistoryReceiptStore(args.checkpoint_root)
    projections = LocalProjectionStore(args.projection_root)

    if existing_session is not None:
        if existing_session.get("schema_version") != _SESSION_SCHEMA_VERSION:
            raise SystemExit("Unsupported live projection qualification session schema")
        if existing_session.get("status") != "PREPARING":
            raise SystemExit("Existing session is not resumable PREPARING state")
        if existing_session.get("account_id") != account_id:
            raise SystemExit("Session account does not match selected Gmail account")
        snapshot_run_id = UUID(str(existing_session["snapshot_run_id"]))
        incremental_run_id = UUID(str(existing_session["prepare_incremental_run_id"]))
        if args.snapshot_run_id is not None and args.snapshot_run_id != snapshot_run_id:
            raise SystemExit("--snapshot-run-id conflicts with persisted session")
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
                "projection_root": str(args.projection_root),
                "snapshot_run_id": str(snapshot_run_id),
                "prepare_incremental_run_id": str(incremental_run_id),
            },
        )

    committed_before = checkpoints.load_committed(account_id)
    if committed_before is None:
        reliable = ReliableIncrementalSync(
            provider,
            artifacts,
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
        if not reliable.recovered_with_full_snapshot:
            raise SystemExit("Prepare did not exercise full snapshot recovery")
        if reliable.full_snapshot_run_id != snapshot_run_id:
            raise SystemExit("Prepare returned an unexpected baseline snapshot run")
        incremental = reliable.incremental
        bootstrap_history_id = reliable.bootstrap_history_id
    elif committed_before.run_id == snapshot_run_id:
        bootstrap_history_id = committed_before.history_id
        incremental = IncrementalHistorySync(
            provider,
            artifacts,
            checkpoints,
            receipt_store=receipts,
        ).run(
            account_id,
            run_id=incremental_run_id,
            max_results=args.max_results,
        )
    elif committed_before.run_id == incremental_run_id:
        incremental = IncrementalHistorySync(
            provider,
            artifacts,
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
            "Qualification checkpoint root contains an unrelated committed checkpoint"
        )

    if bootstrap_history_id is None:
        raise SystemExit("Prepare did not establish a baseline Gmail History frontier")

    initial_projection = ProjectCurrentState(artifacts, projections).run(
        account_id=account_id,
        baseline_run_id=snapshot_run_id,
        baseline_dataset_version=str(snapshot_run_id),
        delta_run_ids=(incremental.run_id,),
        candidate_only=False,
    )
    current = projections.load_current(account_id)

    checks = {
        "full_snapshot_recovery_exercised": True,
        "baseline_history_id_present": bool(bootstrap_history_id),
        "catchup_committed": incremental.output_history_id
        == (checkpoints.load_committed(account_id) or committed_before).history_id,
        "initial_projection_promoted": initial_projection.promoted,
        "initial_projection_converged": (
            initial_projection.logical_digest == initial_projection.rebuild_digest
        ),
        "initial_projection_target_matches_checkpoint": (
            initial_projection.target_history_id == incremental.output_history_id
        ),
        "initial_current_pointer_matches_projection": (
            current is not None
            and current.projection_id == initial_projection.projection_id
            and current.projected_checkpoint_history_id == incremental.output_history_id
        ),
        "oauth_secrets_absent_from_artifacts": not _tree_contains_secret(
            args.artifact_root, secrets
        ),
        "oauth_secrets_absent_from_control": not _tree_contains_secret(
            args.checkpoint_root, secrets
        ),
        "oauth_secrets_absent_from_projection": not _tree_contains_secret(
            args.projection_root, secrets
        ),
    }
    if not all(checks.values()):
        raise SystemExit(f"Prepare projection checks failed: {checks}")

    session = {
        "schema_version": _SESSION_SCHEMA_VERSION,
        "status": "PREPARED",
        "qualified_version": __version__,
        "prepared_at": datetime.now(UTC).isoformat(),
        "account_id": account_id,
        "full_mailbox": True,
        "artifact_root": str(args.artifact_root),
        "checkpoint_root": str(args.checkpoint_root),
        "projection_root": str(args.projection_root),
        "baseline_run_id": str(snapshot_run_id),
        "baseline_dataset_version": str(snapshot_run_id),
        "bootstrap_history_id": bootstrap_history_id,
        "prepare_incremental_run_id": str(incremental.run_id),
        "baseline_committed_history_id": incremental.output_history_id,
        "initial_projection_id": initial_projection.projection_id,
        "initial_projection_digest": initial_projection.logical_digest,
        "prepare_checks": checks,
    }
    _write_private_json(args.session, session)

    print("prepare_gate: PASS")
    print(f"session: {args.session}")
    print("Next, perform these bounded Gmail UI mutations:")
    print("  1. Send one disposable test message to yourself.")
    print("  2. Add one disposable label to that message.")
    print("  3. Remove the disposable label from that message.")
    print("  4. Permanently delete the disposable message.")
    return 0


def _verify(args: argparse.Namespace) -> int:
    if not 1 <= args.max_results <= 500:
        raise SystemExit("--max-results must be between 1 and 500")

    session = _read_object(args.session)
    if session.get("schema_version") != _SESSION_SCHEMA_VERSION:
        raise SystemExit("Unsupported live projection qualification session schema")
    if session.get("status") != "PREPARED":
        raise SystemExit("Qualification session is not ready for verify")
    if session.get("full_mailbox") is not True:
        raise SystemExit("Qualification baseline is not full-mailbox")

    provider = build_provider(Path("credentials.json"), args.credential_dir)
    account_id, credential_payload = provider.store.load_credentials_info(args.account_id)
    if session.get("account_id") != account_id:
        raise SystemExit("Session account does not match selected Gmail account")

    artifact_root = Path(str(session["artifact_root"]))
    checkpoint_root = Path(str(session["checkpoint_root"]))
    projection_root = Path(str(session["projection_root"]))
    if (
        artifact_root != args.artifact_root
        or checkpoint_root != args.checkpoint_root
        or projection_root != args.projection_root
    ):
        raise SystemExit("Qualification roots differ from prepare session")

    secrets = _credential_secrets(credential_payload)
    artifacts = LocalArtifactStore(artifact_root)
    checkpoints = LocalCheckpointStore(checkpoint_root)
    receipts = LocalHistoryReceiptStore(checkpoint_root)
    projections = LocalProjectionStore(projection_root)

    committed_before = checkpoints.load_committed(account_id)
    if (
        committed_before is None
        or committed_before.history_id != session.get("baseline_committed_history_id")
    ):
        raise SystemExit("Qualification checkpoint moved since prepare")

    run_id = uuid.uuid4()
    first = IncrementalHistorySync(
        provider,
        artifacts,
        checkpoints,
        receipt_store=receipts,
    ).run(
        account_id,
        run_id=run_id,
        max_results=args.max_results,
    )

    events = _history_events(artifacts, run_id)
    counts = _event_type_counts(events)
    required = tuple(args.require_event_type or _EVENT_TYPES)
    required_present = all(counts[event_type] > 0 for event_type in required)

    same_run = IncrementalHistorySync(
        provider,
        artifacts,
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
        artifacts,
        replay_checkpoints,
        receipt_store=receipts,
    ).run(
        account_id,
        run_id=replay_run_id,
        max_results=args.max_results,
    )

    baseline_run_id = UUID(str(session["baseline_run_id"]))
    prepare_incremental_run_id = UUID(str(session["prepare_incremental_run_id"]))
    projector = ProjectCurrentState(artifacts, projections)
    projected = projector.run(
        account_id=account_id,
        baseline_run_id=baseline_run_id,
        baseline_dataset_version=str(session["baseline_dataset_version"]),
        delta_run_ids=(prepare_incremental_run_id, first.run_id),
        candidate_only=False,
    )
    replay_projection = projector.run(
        account_id=account_id,
        baseline_run_id=baseline_run_id,
        baseline_dataset_version=str(session["baseline_dataset_version"]),
        delta_run_ids=(prepare_incremental_run_id, first.run_id),
        candidate_only=False,
    )
    current = projections.load_current(account_id)
    mutation_checks = _projection_mutation_checks(
        store=projections,
        dataset_store=LocalProjectionDatasetStore(projections),
        account_id=account_id,
        events=events,
    )

    checks = {
        "real_history_events_observed": first.events_persisted > 0,
        "required_event_types_present": required_present,
        "events_all_processed": _all_events_processed(events),
        "checkpoint_advanced": first.output_history_id != first.input_history_id,
        "same_run_recovered_without_reduction": same_run.recovered,
        "same_run_output_stable": same_run.output_history_id == first.output_history_id,
        "cross_run_receipts_replayed": cross_run.replayed_events >= first.events_persisted,
        "cross_run_no_message_rehydration": cross_run.messages_hydrated == 0,
        "cross_run_output_stable": cross_run.output_history_id == first.output_history_id,
        "projection_promoted": projected.promoted,
        "projection_target_matches_checkpoint": (
            projected.target_history_id == first.output_history_id
        ),
        "incremental_equals_full_rebuild": (
            projected.logical_digest == projected.rebuild_digest
        ),
        "projection_replay_already_current": replay_projection.already_current,
        "projection_replay_digest_stable": (
            replay_projection.logical_digest == projected.logical_digest
        ),
        "current_pointer_matches_target": (
            current is not None
            and current.projection_id == projected.projection_id
            and current.projected_checkpoint_history_id == first.output_history_id
        ),
        **mutation_checks,
        "oauth_secrets_absent_from_artifacts": not _tree_contains_secret(
            artifact_root, secrets
        ),
        "oauth_secrets_absent_from_control": not _tree_contains_secret(
            checkpoint_root, secrets
        ),
        "oauth_secrets_absent_from_projection": not _tree_contains_secret(
            projection_root, secrets
        ),
    }
    passed = all(checks.values())
    stable_eligible = set(required) == set(_EVENT_TYPES)

    evidence = {
        "schema_version": _EVIDENCE_SCHEMA_VERSION,
        "qualified_version": __version__,
        "qualified_at": datetime.now(UTC).isoformat(),
        "account_id": account_id,
        "live_gate_passed": passed,
        "stable_eligible": stable_eligible,
        "required_event_types": list(required),
        "observed_event_types": dict(sorted(counts.items())),
        "prepare": {
            "baseline_run_id": str(baseline_run_id),
            "bootstrap_history_id_present": bool(session.get("bootstrap_history_id")),
            "prepare_incremental_run_id": str(prepare_incremental_run_id),
            "initial_projection_id": session.get("initial_projection_id"),
            "prepare_checks": session.get("prepare_checks"),
        },
        "incremental": {
            "run_id": str(first.run_id),
            "input_history_id": first.input_history_id,
            "output_history_id": first.output_history_id,
            "events_persisted": first.events_persisted,
            "replayed_events": first.replayed_events,
            "messages_hydrated": first.messages_hydrated,
            "message_tombstones": first.message_tombstones,
            "label_mutations": first.label_mutations,
        },
        "projection": {
            "projection_id": projected.projection_id,
            "input_history_id": projected.input_history_id,
            "target_history_id": projected.target_history_id,
            "logical_digest": projected.logical_digest,
            "rebuild_digest": projected.rebuild_digest,
            "incremental_batch_count": projected.incremental_batch_count,
            "rebuild_batch_count": projected.rebuild_batch_count,
            "promoted": projected.promoted,
            "replay_already_current": replay_projection.already_current,
        },
        "checks": checks,
    }
    _write_private_json(args.output, evidence)

    print(f"live_gate: {'PASS' if passed else 'FAIL'}")
    print(f"stable_eligible: {str(stable_eligible).lower()}")
    print(f"evidence: {args.output}")
    return 0 if passed and stable_eligible else 1


def main() -> int:
    _ensure_version()
    args = _parser().parse_args()
    if args.phase == "prepare":
        return _prepare(args)
    return _verify(args)


if __name__ == "__main__":
    raise SystemExit(main())
