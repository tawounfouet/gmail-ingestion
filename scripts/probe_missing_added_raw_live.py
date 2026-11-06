#!/usr/bin/env python3
"""Probe unresolved History additions using Gmail minimal GET, without RAW writes.

Observations are about current Gmail availability, not historic existence.
No message IDs, subjects, mail bodies or credentials are emitted.
"""
from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from audit_missing_added_raw import _baseline_matches
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.api import GmailApiClient, GmailApiError, GmailMessageNotFoundError
from gmail_ingestion.auth import GmailAuthError, build_provider
from gmail_ingestion.config import default_credential_dir
from gmail_ingestion.domain import GmailHistoryEventType
from gmail_ingestion.persistence import LocalProjectionBatchReader, RawMessageWriter
from gmail_ingestion.projection import validate_delta_chain



class MinimalClient(Protocol):
    def get_message(
        self, message_id: str, *, format: str = "minimal"
    ) -> Mapping[str, Any]: ...


def unresolved_ids(
    root: Path,
    account: str,
    baseline_id: UUID,
    delta_ids: tuple[UUID, ...],
) -> tuple[set[str], int, str]:
    """Return unresolved unique IDs, unresolved event count and History frontier."""
    if not delta_ids:
        raise ValueError("No delta run supplied")
    store = LocalArtifactStore(root)
    folder = store.run_root("gmail.projection_baseline", baseline_id)
    manifest = json.loads((folder / "baseline_manifest.json").read_text())
    if (
        not isinstance(manifest, dict)
        or manifest.get("status") != "COMPLETED"
        or manifest.get("account_id") != account
        or manifest.get("source_snapshot_run_id") != str(baseline_id)
    ):
        raise ValueError("Invalid baseline completion/identity evidence")
    batches = LocalProjectionBatchReader(store).read_many(delta_ids)
    events = validate_delta_chain(
        account_id=account,
        start_history_id=manifest["history_id"],
        target_history_id=batches[-1].output_history_id,
        batches=batches,
    )
    ordered = sorted(
        events, key=lambda e: (int(e.history_id), e.event_ordinal, e.event_id)
    )
    raw = RawMessageWriter(store)
    later_deleted: set[str] = set()
    candidates: list[tuple[str, UUID]] = []
    for event in reversed(ordered):
        if event.event_type is GmailHistoryEventType.MESSAGE_DELETED:
            later_deleted.add(event.message_id)
        elif (
            event.event_type is GmailHistoryEventType.MESSAGE_ADDED
            and event.message_id not in later_deleted
            and not raw.exists(event.run_id, event.message_id)
        ):
            candidates.append((event.message_id, event.run_id))
    wanted = {message_id for message_id, _ in candidates}
    baseline_present = _baseline_matches(folder, manifest, wanted) if wanted else set()
    all_runs = (baseline_id, *delta_ids)
    unresolved = [
        message_id
        for message_id, own_run in candidates
        if message_id not in baseline_present
        and not any(
            raw.exists(run_id, message_id) for run_id in all_runs
            if run_id != own_run
        )
    ]
    return set(unresolved), len(unresolved), batches[-1].output_history_id


def probe(
    ids: set[str],
    client: MinimalClient,
    *,
    history_id: str,
    event_count: int,
    max_probes: int,
) -> dict[str, object]:
    """Use only minimal GET and abort on errors other than explicit Gmail 404."""
    if max_probes < 1 or len(ids) > max_probes:
        raise ValueError("Number of Gmail GET requests exceeds safety limit")
    available = missing = 0
    for message_id in sorted(ids):
        try:
            response = client.get_message(message_id, format="minimal")
        except GmailMessageNotFoundError:
            missing += 1
        except GmailApiError as exc:
            raise RuntimeError("Non-404 Gmail API failure; probe incomplete") from exc
        else:
            if response.get("id") != message_id:
                raise ValueError("Gmail response ID mismatch")
            available += 1
    return {
        "status": "COMPLETE",
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "historical_target_history_id": history_id,
        "historical_unresolved_add_events": event_count,
        "unique_ids_probed": len(ids),
        "accessible_now": available,
        "not_found_404_now": missing,
        "gmail_api_calls": len(ids),
        "format": "minimal",
        "raw_artifact_writes": 0,
        "checkpoint_writes": 0,
        "caveat": "Current 404/200 cannot prove historical state at target History ID.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", required=True, type=Path)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--baseline-run-id", required=True, type=UUID)
    parser.add_argument("--delta-run-id", required=True, action="append", type=UUID)
    parser.add_argument("--credential-dir", type=Path, default=default_credential_dir())
    parser.add_argument("--max-probes", type=int, default=100)
    args = parser.parse_args()
    try:
        ids, count, history = unresolved_ids(
            args.artifact_root, args.account_id,
            args.baseline_run_id, tuple(args.delta_run_id),
        )
        if len(ids) > args.max_probes:
            raise ValueError("Probe limit exceeded")
        if ids:
            provider = build_provider(Path("credentials.json"), args.credential_dir)
            authenticated, credentials = provider.load(args.account_id)
            if authenticated != args.account_id:
                raise ValueError("Authenticated account differs from requested account")
            client = GmailApiClient(credentials=credentials).messages
        else:
            client = None
        if client is None:
            result = {
                "status": "COMPLETE", "historical_target_history_id": history,
                "historical_unresolved_add_events": count, "unique_ids_probed": 0,
                "accessible_now": 0, "not_found_404_now": 0, "gmail_api_calls": 0,
                "raw_artifact_writes": 0, "checkpoint_writes": 0,
            }
        else:
            result = probe(
                ids, client, history_id=history, event_count=count,
                max_probes=args.max_probes,
            )
    except (OSError, ValueError, RuntimeError, GmailAuthError) as exc:
        print(f"PROBE FAILED: exception_type={type(exc).__name__}")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
