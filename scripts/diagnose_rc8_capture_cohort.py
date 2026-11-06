#!/usr/bin/env python3
"""Diagnose a blocked RC8 current-source recovery cohort without writing RAW.

Read-only Gmail FULL hydration for a bounded list from a private candidate
plan, followed by Gmail History change classification since its frozen source
frontier. Only aggregate counts are printed. No private IDs, message content,
or account secrets are written or printed. The result is not a release GO.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from gmail_ingestion.api import (
    GmailApiClient,
    GmailApiError,
    GmailHistoryExpiredError,
    GmailMessageNotFoundError,
)
from gmail_ingestion.auth import GmailAuthError, build_provider
from gmail_ingestion.config import default_credential_dir

from capture_rc8_current_source_evidence import (
    SourceClient,
    SourceCaptureError,
    _history_record_ids,
    _load_candidate,
)


def _history_summary(
    client: SourceClient,
    *,
    source_history_id: str,
    required_ids: set[str],
    max_history_pages: int,
) -> tuple[str, set[str], set[str], int]:
    """Return source frontier, changed cohort IDs, explicitly deleted IDs, pages."""
    if max_history_pages < 1:
        raise SourceCaptureError("History page cap must be positive")
    start = int(source_history_id)
    end = start
    token: str | None = None
    seen_tokens: set[str] = set()
    changed: set[str] = set()
    deleted: set[str] = set()
    previous_record = start

    for page_number in range(1, max_history_pages + 1):
        page = client.history.list_history(
            start_history_id=source_history_id,
            page_token=token,
            max_results=500,
        )
        value = page.get("historyId")
        if not isinstance(value, str) or not value.isdecimal():
            raise SourceCaptureError("Invalid Gmail History response frontier")
        if int(value) < start:
            raise SourceCaptureError("Gmail History response regressed")
        end = max(end, int(value))
        records = page.get("history") or []
        if not isinstance(records, list):
            raise SourceCaptureError("Invalid Gmail History record collection")
        for record in records:
            if not isinstance(record, Mapping):
                raise SourceCaptureError("Malformed Gmail History record")
            row_id = record.get("id")
            if not isinstance(row_id, str) or not row_id.isdecimal():
                raise SourceCaptureError("Invalid Gmail History record frontier")
            if not previous_record < int(row_id) <= end:
                raise SourceCaptureError("Non-monotonic or out-of-range Gmail History record")
            previous_record = int(row_id)
            changed.update(_history_record_ids(record) & required_ids)
            changes = record.get("messagesDeleted") or []
            if not isinstance(changes, list):
                raise SourceCaptureError("Malformed Gmail deletion evidence")
            for change in changes:
                if not isinstance(change, Mapping):
                    raise SourceCaptureError("Malformed Gmail deletion entry")
                nested = change.get("message")
                if not isinstance(nested, Mapping):
                    raise SourceCaptureError("Gmail deletion lacks message")
                mid = nested.get("id")
                if not isinstance(mid, str) or not mid:
                    raise SourceCaptureError("Invalid Gmail deleted message ID")
                if mid in required_ids:
                    deleted.add(mid)
        next_token = page.get("nextPageToken")
        if next_token is None:
            return str(end), changed, deleted, page_number
        if (
            not isinstance(next_token, str)
            or not next_token
            or next_token in seen_tokens
        ):
            raise SourceCaptureError("Repeated or invalid Gmail History page token")
        seen_tokens.add(next_token)
        token = next_token
    raise SourceCaptureError("History page cap reached; diagnosis incomplete")


def diagnose_cohort(
    client: SourceClient,
    *,
    candidate: Mapping[str, Any],
    max_messages: int = 200,
    max_history_pages: int = 100,
) -> dict[str, object]:
    """Hydrate up to max_messages, classify 404s and bound source History drift."""
    source_history_id = candidate.get("source_history_id")
    required = candidate.get("private_recovery_required_ids")
    if (
        not isinstance(source_history_id, str)
        or not source_history_id.isdecimal()
        or not isinstance(required, list)
        or any(not isinstance(mid, str) or not mid for mid in required)
        or len(required) != len(set(required))
        or not isinstance(candidate.get("candidate_id"), str)
        or not 1 <= max_messages
        or len(required) > max_messages
    ):
        raise SourceCaptureError("Invalid or oversized private recovery cohort")
    ids = set(required)
    not_found: set[str] = set()
    invalid: set[str] = set()
    out_of_scope: set[str] = set()
    newer_than_inventory: set[str] = set()
    retrievable: set[str] = set()

    for mid in sorted(ids):
        try:
            payload = client.messages.get_message(mid, format="full")
        except GmailMessageNotFoundError:
            not_found.add(mid)
            continue
        if (
            not isinstance(payload, Mapping)
            or payload.get("id") != mid
            or not isinstance(payload.get("threadId"), str)
            or not isinstance(payload.get("payload"), Mapping)
            or not isinstance(payload.get("labelIds"), list)
            or any(not isinstance(label, str) for label in payload["labelIds"])
        ):
            invalid.add(mid)
            continue
        history_id = payload.get("historyId")
        if not isinstance(history_id, str) or not history_id.isdecimal():
            invalid.add(mid)
            continue
        if int(history_id) > int(source_history_id):
            newer_than_inventory.add(mid)
        if "SPAM" in payload["labelIds"] or "TRASH" in payload["labelIds"]:
            out_of_scope.add(mid)
        retrievable.add(mid)

    end, changed, deleted, pages = _history_summary(
        client,
        source_history_id=source_history_id,
        required_ids=ids,
        max_history_pages=max_history_pages,
    )

    blockers = (
        not_found | invalid | out_of_scope | newer_than_inventory | changed
    )
    return {
        "status": (
            "CAPTURE_PREFLIGHT_PASS_NOT_RELEASE_QUALIFIED"
            if not blockers
            else "BLOCKED_SOURCE_COHORT_REVALIDATION_REQUIRED"
        ),
        "candidate_id": candidate["candidate_id"],
        "frozen_source_history_id": source_history_id,
        "history_checked_through": end,
        "history_pages": pages,
        "required_message_count": len(ids),
        "retrievable_full_count": len(retrievable),
        "message_not_found_count": len(not_found),
        "not_found_with_post_frontier_delete_count": len(not_found & deleted),
        "not_found_without_post_frontier_delete_count": len(not_found - deleted),
        "malformed_full_payload_count": len(invalid),
        "currently_outside_source_scope_count": len(out_of_scope),
        "payload_newer_than_source_frontier_count": len(newer_than_inventory),
        "required_message_ids_changed_since_inventory_count": len(changed),
        "required_message_ids_with_explicit_delete_count": len(deleted),
        "distinct_required_ids_blocked_count": len(blockers),
        "gmail_full_get_calls_attempted": len(ids),
        "gmail_history_pages": pages,
        "gmail_message_content_persisted": False,
        "persistent_writes": 0,
        "historical_raw_overwritten": False,
        "current_pointer_promoted": False,
        "stable_release_qualified": False,
        "caveat": (
            "A 404 without post-frontier delete is not proof of deletion. "
            "Source membership is frozen at a prior History boundary; "
            "a successful preflight would not alone establish a race-free "
            "capture or authorize a Current promotion."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--candidate-evidence", type=Path, required=True)
    parser.add_argument(
        "--credential-dir", type=Path, default=default_credential_dir()
    )
    parser.add_argument("--max-messages", type=int, default=200)
    parser.add_argument("--max-history-pages", type=int, default=100)
    args = parser.parse_args()
    try:
        candidate = _load_candidate(args.candidate_evidence)
        if candidate.get("account_id") != args.account_id:
            raise SourceCaptureError("Authenticated and candidate account differ")
        provider = build_provider(Path("credentials.json"), args.credential_dir)
        resolved, credentials = provider.load(args.account_id)
        if resolved != args.account_id:
            raise SourceCaptureError("Stored credentials account differs")
        report = diagnose_cohort(
            GmailApiClient(credentials=credentials),
            candidate=candidate,
            max_messages=args.max_messages,
            max_history_pages=args.max_history_pages,
        )
    except (
        OSError,
        ValueError,
        RuntimeError,
        TypeError,
        GmailApiError,
        GmailAuthError,
    ) as exc:
        print("RC8 COHORT DIAGNOSIS FAILED: exception_type=" + type(exc).__name__)
        if isinstance(exc, GmailHistoryExpiredError):
            print("reason: History cursor expired; no snapshot started")
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"].startswith("CAPTURE_PREFLIGHT_PASS") else 3


if __name__ == "__main__":
    raise SystemExit(main())
