#!/usr/bin/env python
"""Qualify Gmail Ingestion 0.3.0 filtered/range behavior on a real mailbox."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion import __version__
from gmail_ingestion.application import FullMessageSnapshot, RangeMessageSnapshot
from gmail_ingestion.auth import build_provider
from gmail_ingestion.config import default_credential_dir
from gmail_ingestion.domain import DateRange

_SECRET_KEYS = ("token", "refresh_token", "client_secret")
_ALLOWED_VERSIONS = {"0.3.0b1", "0.3.0"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Qualify Gmail Ingestion 0.3.0 search/date-range behavior against a real "
            "mailbox without emitting Gmail message content."
        )
    )
    parser.add_argument("--account-id")
    parser.add_argument("--credential-dir", type=Path, default=default_credential_dir())
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path(".gmail-ingestion-live-0.3.0"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("qualification/v0.3.0-live-evidence.json"),
    )
    parser.add_argument("--query", default="has:attachment")
    parser.add_argument("--label-id", default="INBOX")
    parser.add_argument("--last-days", type=int, default=30)
    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--max-attempts", type=int, default=5)
    parser.add_argument(
        "--require-query-hit",
        action="store_true",
        help="Fail unless the query snapshot returns at least one message.",
    )
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


def _manifest(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"Invalid manifest object: {path}")
    return payload


def _completed_without_fetch_loss(result: Any) -> bool:
    return (
        result.manifest_status == "COMPLETED"
        and result.discovered_count == result.fetched_count
        and result.failed_count == 0
    )


def main() -> int:
    args = _parse_args()
    if __version__ not in _ALLOWED_VERSIONS:
        raise SystemExit(
            "Filtered-ingestion qualification requires gmail-ingestion "
            f"{sorted(_ALLOWED_VERSIONS)}, found {__version__}"
        )
    if args.last_days < 1:
        raise SystemExit("--last-days must be >= 1")
    if args.sample_size < 1:
        raise SystemExit("--sample-size must be >= 1")
    if not args.query.strip():
        raise SystemExit("--query must not be empty")
    if not args.label_id.strip():
        raise SystemExit("--label-id must not be empty")

    provider = build_provider(Path("credentials.json"), args.credential_dir)
    account_id, credential_payload = provider.store.load_credentials_info(args.account_id)
    secrets = _credential_secrets(credential_payload)
    store = LocalArtifactStore(args.artifact_root)

    query_result = FullMessageSnapshot(provider, store).run(
        account_id,
        query=args.query,
        message_limit=args.sample_size,
        max_workers=args.max_workers,
        max_attempts=args.max_attempts,
    )
    label_result = FullMessageSnapshot(provider, store).run(
        account_id,
        label_ids=(args.label_id,),
        message_limit=args.sample_size,
        max_workers=args.max_workers,
        max_attempts=args.max_attempts,
    )
    range_result = RangeMessageSnapshot(provider, store).run(
        DateRange(last_days=args.last_days),
        account_id,
        message_limit=args.sample_size,
        max_workers=args.max_workers,
        max_attempts=args.max_attempts,
    )
    range_resumed = RangeMessageSnapshot(provider, store).run(
        DateRange(last_days=args.last_days),
        account_id,
        run_id=range_result.run_id,
        message_limit=args.sample_size,
        max_workers=args.max_workers,
        max_attempts=args.max_attempts,
    )
    combined_result = RangeMessageSnapshot(provider, store).run(
        DateRange(last_days=args.last_days),
        account_id,
        query=args.query,
        label_ids=(args.label_id,),
        message_limit=args.sample_size,
        max_workers=args.max_workers,
        max_attempts=args.max_attempts,
    )

    query_manifest = _manifest(query_result.manifest_path)
    label_manifest = _manifest(label_result.manifest_path)
    range_manifest = _manifest(range_result.manifest_path)
    resumed_manifest = _manifest(range_resumed.manifest_path)
    combined_manifest = _manifest(combined_result.manifest_path)

    query_scope = query_manifest.get("scope", {})
    label_scope = label_manifest.get("scope", {})
    range_scope = range_manifest.get("scope", {})
    combined_scope = combined_manifest.get("scope", {})
    fingerprints = {
        scope.get("fingerprint")
        for scope in (query_scope, label_scope, range_scope, combined_scope)
        if isinstance(scope, dict)
    }

    range_effective = range_manifest.get("effective_filter", {})
    resumed_effective = resumed_manifest.get("effective_filter", {})
    combined_effective = combined_manifest.get("effective_filter", {})

    checks = {
        "query_completed": _completed_without_fetch_loss(query_result),
        "query_hit_when_required": (
            not args.require_query_hit or query_result.discovered_count > 0
        ),
        "query_scope_filtered": (
            isinstance(query_scope, dict) and query_scope.get("kind") == "filtered"
        ),
        "query_effective_q_recorded": (
            isinstance(query_manifest.get("effective_filter"), dict)
            and query_manifest["effective_filter"].get("gmail_q") == args.query
        ),
        "label_completed": _completed_without_fetch_loss(label_result),
        "label_scope_filtered": (
            isinstance(label_scope, dict) and label_scope.get("kind") == "filtered"
        ),
        "range_completed": _completed_without_fetch_loss(range_result),
        "range_scope_recorded": (
            isinstance(range_scope, dict) and range_scope.get("kind") == "range"
        ),
        "relative_range_resolved": (
            isinstance(range_effective, dict)
            and bool(range_effective.get("resolved_after"))
            and bool(range_effective.get("resolution_timestamp"))
        ),
        "range_resume_detected": range_resumed.resumed,
        "range_resume_fingerprint_stable": (
            isinstance(range_scope, dict)
            and isinstance(resumed_manifest.get("scope"), dict)
            and resumed_manifest["scope"].get("fingerprint")
            == range_scope.get("fingerprint")
        ),
        "range_resume_resolution_stable": (
            isinstance(range_effective, dict)
            and isinstance(resumed_effective, dict)
            and resumed_effective.get("resolved_after")
            == range_effective.get("resolved_after")
            and resumed_effective.get("resolution_timestamp")
            == range_effective.get("resolution_timestamp")
        ),
        "combined_completed": _completed_without_fetch_loss(combined_result),
        "combined_scope_range": (
            isinstance(combined_scope, dict) and combined_scope.get("kind") == "range"
        ),
        "combined_q_contains_query_and_date": (
            isinstance(combined_effective, dict)
            and isinstance(combined_effective.get("gmail_q"), str)
            and args.query in combined_effective["gmail_q"]
            and "after:" in combined_effective["gmail_q"]
        ),
        "combined_label_recorded": (
            isinstance(combined_effective, dict)
            and args.label_id in combined_effective.get("label_ids", [])
        ),
        "scope_identities_distinct": len(fingerprints) == 4,
        "oauth_secrets_absent_from_artifacts": not _artifacts_contain_secret(
            args.artifact_root,
            secrets,
        ),
    }
    passed = all(checks.values())

    def summary(result: Any, manifest: dict[str, Any]) -> dict[str, Any]:
        scope = manifest.get("scope", {})
        effective = manifest.get("effective_filter", {})
        return {
            "run_id": str(result.run_id),
            "messages_discovered": result.discovered_count,
            "messages_fetched": result.fetched_count,
            "messages_failed": result.failed_count,
            "manifest_status": result.manifest_status,
            "scope_kind": scope.get("kind") if isinstance(scope, dict) else None,
            "scope_fingerprint": (
                scope.get("fingerprint") if isinstance(scope, dict) else None
            ),
            "resolved_after": (
                effective.get("resolved_after") if isinstance(effective, dict) else None
            ),
            "resolved_before": (
                effective.get("resolved_before") if isinstance(effective, dict) else None
            ),
        }

    evidence = {
        "schema_version": 1,
        "qualified_version": __version__,
        "qualified_at": datetime.now(UTC).isoformat(),
        "account_id": account_id,
        "live_gate_passed": passed,
        "settings": {
            "query": args.query,
            "label_id": args.label_id,
            "last_days": args.last_days,
            "sample_size": args.sample_size,
            "require_query_hit": args.require_query_hit,
        },
        "query_snapshot": summary(query_result, query_manifest),
        "label_snapshot": summary(label_result, label_manifest),
        "range_snapshot": summary(range_result, range_manifest),
        "range_resume": summary(range_resumed, resumed_manifest),
        "combined_snapshot": summary(combined_result, combined_manifest),
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
