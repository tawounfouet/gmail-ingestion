#!/usr/bin/env python
"""Run the real-mailbox release gate without emitting Gmail content or OAuth secrets."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion import __version__
from gmail_ingestion.application import FullMessageSnapshot, GetMailboxProfile, IngestLabels
from gmail_ingestion.auth import build_provider
from gmail_ingestion.config import default_credential_dir

_SECRET_KEYS = ("token", "refresh_token", "client_secret")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Qualify Gmail Ingestion 0.1.0 against a real Gmail mailbox. "
            "A bounded sample is used by default so large mailboxes remain safe."
        )
    )
    parser.add_argument("--account-id")
    parser.add_argument("--credential-dir", type=Path, default=default_credential_dir())
    parser.add_argument("--artifact-root", type=Path, default=Path(".gmail-ingestion-live"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("qualification/v0.1.0-live-evidence.json"),
    )
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument(
        "--sample-size",
        type=int,
        default=50,
        help="Maximum real Gmail messages to qualify. Defaults to 50.",
    )
    parser.add_argument(
        "--full-mailbox",
        action="store_true",
        help="Qualify the entire mailbox instead of the bounded sample.",
    )
    parser.add_argument("--download-attachments", action="store_true")
    parser.add_argument("--max-attachment-bytes", type=int)
    parser.add_argument("--require-attachment", action="store_true")
    return parser.parse_args()


def _credential_secrets(payload: dict[str, Any]) -> tuple[bytes, ...]:
    values: list[bytes] = []
    for key in _SECRET_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value:
            values.append(value.encode("utf-8"))
    return tuple(values)


def _artifacts_contain_secret(root: Path, secrets: tuple[bytes, ...]) -> bool:
    if not secrets or not root.exists():
        return False
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if any(secret in data for secret in secrets):
            return True
    return False


def main() -> int:
    args = _parse_args()
    if __version__ != "0.1.0":
        raise SystemExit(
            f"Stable qualification requires gmail-ingestion 0.1.0, found {__version__}"
        )

    if args.sample_size < 1:
        raise SystemExit("--sample-size must be >= 1")

    message_limit = None if args.full_mailbox else args.sample_size
    provider = build_provider(Path("credentials.json"), args.credential_dir)
    account_id, credential_payload = provider.store.load_credentials_info(args.account_id)
    secrets = _credential_secrets(credential_payload)
    store = LocalArtifactStore(args.artifact_root)

    first_profile = GetMailboxProfile(provider).run(account_id)
    second_profile = GetMailboxProfile(provider).run(account_id)
    labels = IngestLabels(provider, store).run(account_id)

    snapshot = FullMessageSnapshot(provider, store).run(
        account_id,
        max_workers=args.max_workers,
        max_attempts=args.max_attempts,
        download_attachments=args.download_attachments,
        max_attachment_bytes=args.max_attachment_bytes,
        message_limit=message_limit,
    )
    resumed = FullMessageSnapshot(provider, store).run(
        account_id,
        run_id=snapshot.run_id,
        max_workers=args.max_workers,
        max_attempts=args.max_attempts,
        download_attachments=args.download_attachments,
        max_attachment_bytes=args.max_attachment_bytes,
        message_limit=message_limit,
    )

    checks = {
        "profile_reusable_credentials": (
            first_profile.account_id == second_profile.account_id == account_id
        ),
        "labels_present": labels.label_count > 0,
        "labels_raw_present": labels.raw_artifact_count > 0,
        "labels_parquet_present": labels.parquet_path.is_file(),
        "snapshot_completed": snapshot.manifest_status == "COMPLETED",
        "sample_bound_respected": (
            message_limit is None or snapshot.discovered_count <= message_limit
        ),
        "snapshot_all_messages_fetched": (
            snapshot.discovered_count == snapshot.fetched_count
            and snapshot.failed_count == 0
        ),
        "mime_complete": (
            snapshot.mime_messages_processed == snapshot.fetched_count
            and snapshot.mime_messages_failed == 0
        ),
        "manifest_present": Path(snapshot.manifest_path).is_file(),
        "resume_detected": resumed.resumed,
        "resume_counts_stable": (
            resumed.discovered_count == snapshot.discovered_count
            and resumed.fetched_count == snapshot.fetched_count
            and resumed.failed_count == snapshot.failed_count
        ),
        "attachment_failures_absent": snapshot.attachments_failed == 0,
        "required_attachment_present": (
            not args.require_attachment or snapshot.attachments_detected > 0
        ),
        "required_attachment_downloaded": (
            not args.require_attachment
            or not args.download_attachments
            or snapshot.attachments_fetched > 0
        ),
        "oauth_secrets_absent_from_artifacts": not _artifacts_contain_secret(
            args.artifact_root,
            secrets,
        ),
    }
    passed = all(checks.values())

    evidence = {
        "schema_version": 1,
        "qualified_version": __version__,
        "qualified_at": datetime.now(UTC).isoformat(),
        "account_id": account_id,
        "live_gate_passed": passed,
        "settings": {
            "download_attachments": args.download_attachments,
            "max_attachment_bytes": args.max_attachment_bytes,
            "require_attachment": args.require_attachment,
            "sample_size": message_limit,
            "full_mailbox": args.full_mailbox,
        },
        "profile": {
            "messages_total": second_profile.messages_total,
            "threads_total": second_profile.threads_total,
            "history_id_present": bool(second_profile.history_id),
        },
        "labels": {
            "count": labels.label_count,
            "raw_artifact_count": labels.raw_artifact_count,
        },
        "snapshot": {
            "run_id": str(snapshot.run_id),
            "pages_scanned": snapshot.pages_scanned,
            "messages_discovered": snapshot.discovered_count,
            "messages_fetched": snapshot.fetched_count,
            "messages_failed": snapshot.failed_count,
            "mime_messages_processed": snapshot.mime_messages_processed,
            "mime_messages_failed": snapshot.mime_messages_failed,
            "attachments_detected": snapshot.attachments_detected,
            "attachments_fetched": snapshot.attachments_fetched,
            "attachments_failed": snapshot.attachments_failed,
            "attachments_skipped": snapshot.attachments_skipped,
            "bytes_downloaded": snapshot.bytes_downloaded,
            "manifest_status": snapshot.manifest_status,
        },
        "resume": {
            "resumed": resumed.resumed,
            "messages_discovered": resumed.discovered_count,
            "messages_fetched": resumed.fetched_count,
            "messages_failed": resumed.failed_count,
        },
        "checks": checks,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"live_gate: {'PASS' if passed else 'FAIL'}")
    print(f"evidence: {args.output}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
