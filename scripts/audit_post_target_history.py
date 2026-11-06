#!/usr/bin/env python3
"""Inspect Gmail History AFTER the frozen projection target, without modifying artifacts.

Read-only Gmail users.history.list queries, bounded and fully paginated. This is
NOT a new incremental sync or an authorization to rewrite historical state.
The CLI emits aggregate counts only, without Gmail message IDs or content.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from compare_recovery_evidence import _manifest
from probe_missing_added_raw_live import unresolved_ids
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.api import GmailApiClient, GmailApiError, GmailHistoryExpiredError
from gmail_ingestion.auth import GmailAuthError, build_provider
from gmail_ingestion.config import default_credential_dir
from gmail_ingestion.persistence.raw_message_writer import RawMessageWriter

_JOB = "gmail.recovery_evidence"
_CHANGE_FIELDS = {
    "messagesAdded": "added",
    "messagesDeleted": "deleted",
    "labelsAdded": "label_added",
    "labelsRemoved": "label_removed",
}


class HistorySweepIncompleteError(RuntimeError):
    """A bounded sweep cannot assert absence of events until pagination ends."""


class HistoryClient(Protocol):
    def list_history(
        self,
        *,
        start_history_id: str,
        page_token: str | None = None,
        max_results: int = 500,
        history_types: Sequence[str] = (),
        label_id: str | None = None,
    ) -> Mapping[str, Any]: ...


def sweep_history(
    history: HistoryClient,
    *,
    start_history_id: str,
    unresolved_ids: set[str],
    not_found_ids: set[str],
    latest_capture_history_ids: set[str],
    max_pages: int = 40,
    page_size: int = 500,
) -> dict[str, object]:
    """Scan every page, then report matched post-frontier changes by cohort."""
    if (
        not start_history_id.isdecimal()
        or not 1 <= page_size <= 500
        or not 1 <= max_pages <= 200
        or not not_found_ids.issubset(unresolved_ids)
        or not latest_capture_history_ids.issubset(unresolved_ids)
    ):
        raise ValueError("Invalid history sweep bounds or source cohorts")
    changes: dict[str, set[str]] = {msg_id: set() for msg_id in unresolved_ids}
    record_count = 0
    relevant_change_records = 0
    page_count = 0
    page_token: str | None = None
    seen_tokens: set[str] = set()
    last_response_frontier: str | None = None

    for _ in range(max_pages):
        response = history.list_history(
            start_history_id=start_history_id,
            page_token=page_token,
            max_results=page_size,
        )
        page_count += 1
        current_frontier = response.get("historyId")
        if (
            not isinstance(current_frontier, str)
            or not current_frontier.isdecimal()
            or int(current_frontier) < int(start_history_id)
        ):
            raise ValueError("Gmail History response has invalid terminal frontier")
        last_response_frontier = current_frontier
        records = response.get("history", [])
        if not isinstance(records, list):
            raise ValueError("Gmail History response history must be a list")
        for record in records:
            if not isinstance(record, Mapping):
                raise ValueError("Gmail History record must be an object")
            record_history = record.get("id")
            if (
                not isinstance(record_history, str)
                or not record_history.isdecimal()
                or int(record_history) <= int(start_history_id)
            ):
                raise ValueError("History record is outside post-target frontier")
            record_count += 1
            for source_field, category in _CHANGE_FIELDS.items():
                entries = record.get(source_field, [])
                if not isinstance(entries, list):
                    raise ValueError("Gmail History change collection is not a list")
                for entry in entries:
                    if not isinstance(entry, Mapping):
                        raise ValueError("Gmail History event must be an object")
                    msg = entry.get("message")
                    if not isinstance(msg, Mapping):
                        raise ValueError("Gmail History event lacks message identity")
                    message_id = msg.get("id")
                    if not isinstance(message_id, str):
                        raise ValueError("Gmail History event has invalid message identity")
                    if message_id in changes:
                        changes[message_id].add(category)
                        relevant_change_records += 1

        next_token = response.get("nextPageToken")
        if next_token is None or next_token == "":
            break
        if not isinstance(next_token, str) or next_token in seen_tokens:
            raise ValueError("Gmail History pagination token invalid or repeated")
        seen_tokens.add(next_token)
        page_token = next_token
    else:
        raise HistorySweepIncompleteError(
            "Gmail History has more pages than the configured safety cap"
        )

    missing_matches = {msg_id: changes[msg_id] for msg_id in not_found_ids}
    found_matches = {
        msg_id: changes[msg_id] for msg_id in unresolved_ids - not_found_ids
    }
    cohort_counts = Counter(
        category for types in changes.values() for category in types
    )
    return {
        "status": "COMPLETE",
        "historical_target_history_id": start_history_id,
        "last_response_history_id": last_response_frontier,
        "observed_at_utc": datetime.now(UTC).isoformat(),
        "pages_scanned": page_count,
        "history_records_scanned": record_count,
        "post_target_change_entries_matching_cohort": relevant_change_records,
        "unresolved_source_message_ids": len(unresolved_ids),
        "captured_current_full_count": len(unresolved_ids - not_found_ids),
        "not_found_404_in_prior_capture": len(not_found_ids),
        "matched_unique_ids_by_change_type": dict(sorted(cohort_counts.items())),
        "404_with_post_target_deletion": sum(
            "deleted" in types for types in missing_matches.values()
        ),
        "404_with_post_target_addition": sum(
            "added" in types for types in missing_matches.values()
        ),
        "404_with_post_target_deletion_without_new_addition": sum(
            "deleted" in types and "added" not in types
            for types in missing_matches.values()
        ),
        "404_without_post_target_deletion_evidence": sum(
            "deleted" not in types for types in missing_matches.values()
        ),
        "captured_full_with_post_target_addition": sum(
            "added" in types for types in found_matches.values()
        ),
        "captured_full_with_post_target_deletion": sum(
            "deleted" in types for types in found_matches.values()
        ),
        "captured_full_with_post_target_label_mutation": sum(
            "label_added" in types or "label_removed" in types
            for types in found_matches.values()
        ),
        "captured_full_history_id_gt_target_and_changed": sum(
            bool(changes[msg_id]) for msg_id in latest_capture_history_ids
        ),
        "gmail_api_calls": page_count,
        "raw_artifact_writes": 0,
        "history_checkpoint_writes": 0,
        "projection_current_pointer_writes": 0,
        "historical_projection_qualified": False,
        "caveat": (
            "Events AFTER the target can establish later deletions or mutations, "
            "but cannot restore missing RAW at the target or prove its exact labels. "
            "Absence of matched events is meaningful only after full pagination. "
            "Gmail History availability is bounded by Google retention."
        ),
    }


def load_cohorts(
    *,
    artifact_root: Path,
    account_id: str,
    baseline_run_id: UUID,
    delta_run_ids: tuple[UUID, ...],
    recovery_run_id: UUID,
) -> tuple[set[str], set[str], set[str], str]:
    """Validate a completed dated recovery manifest against committed local evidence."""
    all_ids, _, target = unresolved_ids(
        artifact_root, account_id, baseline_run_id, delta_run_ids
    )
    store = LocalArtifactStore(artifact_root)
    captured_ids, not_found_ids, _ = _manifest(
        store, recovery_run_id,
        account_id=account_id,
        baseline_run_id=baseline_run_id,
        delta_run_ids=delta_run_ids,
        expected_ids=all_ids,
        target_history_id=target,
    )
    reader = RawMessageWriter(store, job_id=_JOB)
    after_target: set[str] = set()
    for message_id in captured_ids:
        payload = reader.read(recovery_run_id, message_id)
        current_history = payload.get("historyId")
        if isinstance(current_history, str) and current_history.isdecimal():
            if int(current_history) > int(target):
                after_target.add(message_id)
    return all_ids, not_found_ids, after_target, target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--baseline-run-id", type=UUID, required=True)
    parser.add_argument("--delta-run-id", type=UUID, action="append", required=True)
    parser.add_argument("--recovery-run-id", type=UUID, required=True)
    parser.add_argument("--credential-dir", type=Path, default=default_credential_dir())
    parser.add_argument("--max-pages", type=int, default=40)
    parser.add_argument("--page-size", type=int, default=500)
    args = parser.parse_args()
    try:
        all_ids, missing_ids, after_target, target = load_cohorts(
            artifact_root=args.artifact_root,
            account_id=args.account_id,
            baseline_run_id=args.baseline_run_id,
            delta_run_ids=tuple(args.delta_run_id),
            recovery_run_id=args.recovery_run_id,
        )
        provider = build_provider(Path("credentials.json"), args.credential_dir)
        resolved, credentials = provider.load(args.account_id)
        if resolved != args.account_id:
            raise ValueError("Authenticated account identity mismatch")
        client = GmailApiClient(credentials=credentials).history
        report = sweep_history(
            client,
            start_history_id=target,
            unresolved_ids=all_ids,
            not_found_ids=missing_ids,
            latest_capture_history_ids=after_target,
            max_pages=args.max_pages,
            page_size=args.page_size,
        )
    except (
        OSError, KeyError, TypeError, ValueError, RuntimeError,
        GmailApiError, GmailAuthError,
    ) as exc:
        print(f"POST-TARGET HISTORY AUDIT FAILED: exception_type={type(exc).__name__}")
        if isinstance(exc, GmailHistoryExpiredError):
            print("reason: Gmail History cursor unavailable; no full resync was started")
        elif isinstance(exc, HistorySweepIncompleteError):
            print("reason: page cap reached; no absence of deletion can be inferred")
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
