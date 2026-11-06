#!/usr/bin/env python3
"""Cross-check frozen RC8 recovery IDs against a new *independent* all-mail list.

The verified all-mail inventory must be newly captured using
observe_current_source_inventory.py after the original 404 diagnostic.
This script compares Gmail users.messages.list membership to FULL GET
results and checks History since the *new* source frontier. It never
writes RAW evidence, changes checkpoints, or exposes message IDs.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from capture_rc8_current_source_evidence import SourceCaptureError, _load_candidate
from compare_source_inventory_ids import read_source_ids
from diagnose_rc8_capture_cohort import _history_summary

from gmail_ingestion.api import (
    GmailApiClient,
    GmailApiError,
    GmailHistoryExpiredError,
    GmailMessageNotFoundError,
)
from gmail_ingestion.auth import GmailAuthError, build_provider
from gmail_ingestion.config import default_credential_dir


def audit_membership(
    client: Any,
    *,
    candidate: Mapping[str, Any],
    current_ids: set[str],
    current_history_id: str,
    max_messages: int = 200,
    max_history_pages: int = 100,
) -> dict[str, object]:
    """Cross-tabulate list vs get at a new source frontier, fail closed on drift."""
    frozen = candidate.get("source_history_id")
    required = candidate.get("private_recovery_required_ids")
    if (
        not isinstance(frozen, str)
        or not frozen.isdecimal()
        or not isinstance(current_history_id, str)
        or not current_history_id.isdecimal()
        or int(current_history_id) < int(frozen)
        or not isinstance(required, list)
        or not all(isinstance(mid, str) and bool(mid) for mid in required)
        or len(set(required)) != len(required)
        or max_messages < 1
        or max_history_pages < 1
        or len(required) > max_messages
    ):
        raise SourceCaptureError("Invalid cohort or fresh source frontier")

    retrievable: set[str] = set()
    not_found: set[str] = set()
    malformed: set[str] = set()
    post_frontier_payload: set[str] = set()
    out_of_scope: set[str] = set()

    for mid in sorted(required):
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
            or any(not isinstance(x, str) for x in payload["labelIds"])
        ):
            malformed.add(mid)
            continue
        message_history_id = payload.get("historyId")
        if not isinstance(message_history_id, str) or not message_history_id.isdecimal():
            malformed.add(mid)
            continue
        if int(message_history_id) > int(current_history_id):
            post_frontier_payload.add(mid)
        if "SPAM" in payload["labelIds"] or "TRASH" in payload["labelIds"]:
            out_of_scope.add(mid)
        retrievable.add(mid)

    changed_frontier, changed, deleted, pages = _history_summary(
        client,
        source_history_id=current_history_id,
        required_ids=set(required),
        max_history_pages=max_history_pages,
    )

    listed_and_retrievable = current_ids & retrievable
    listed_but_404 = current_ids & not_found
    unlisted_but_retrievable = retrievable - current_ids
    unlisted_and_404 = not_found - current_ids
    listed_malformed = current_ids & malformed
    unlisted_malformed = malformed - current_ids

    contradictions = listed_but_404 | unlisted_but_retrievable | malformed
    drift = changed | post_frontier_payload
    if contradictions:
        status = "BLOCKED_SOURCE_LIST_GET_INCONSISTENCY"
    elif drift:
        status = "BLOCKED_SOURCE_CHANGED_AFTER_REOBSERVATION"
    elif out_of_scope:
        status = "FROZEN_SCOPE_NO_LONGER_APPLIES"
    elif unlisted_and_404:
        status = "REFRESH_FROZEN_RC8_PLAN_REQUIRED"
    else:
        status = "SOURCE_COHORT_CONSISTENT_NOT_RELEASE_QUALIFIED"

    return {
        "status": status,
        "candidate_id": candidate.get("candidate_id"),
        "frozen_source_history_id": frozen,
        "new_source_history_id": current_history_id,
        "history_checked_through": changed_frontier,
        "required_ids_checked": len(required),
        "new_all_mail_message_count": len(current_ids),
        "listed_and_full_retrievable_count": len(listed_and_retrievable),
        "listed_but_get_404_count": len(listed_but_404),
        "not_listed_but_full_retrievable_count": len(unlisted_but_retrievable),
        "not_listed_and_get_404_count": len(unlisted_and_404),
        "malformed_payload_while_listed_count": len(listed_malformed),
        "malformed_payload_not_listed_count": len(unlisted_malformed),
        "current_spam_trash_scope_count": len(out_of_scope),
        "payload_newer_than_new_source_count": len(post_frontier_payload),
        "changed_since_new_source_count": len(changed),
        "deleted_since_new_source_count": len(deleted),
        "distinct_cohort_ids_with_blockers_count": len(
            contradictions | drift | out_of_scope | unlisted_and_404
        ),
        "history_pages": pages,
        "message_bodies_persisted": 0,
        "gmail_writes": 0,
        "candidate_source_inventory_rewritten": False,
        "historical_raw_overwritten": False,
        "current_pointer_promoted": False,
        "stable_release_qualified": False,
        "note": (
            "A message absent in a new list and returning 404 has observed "
            "current absence, NOT a historically proven deletion. "
            "A new scoped inventory and candidate identity are required."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--candidate-evidence", type=Path, required=True)
    parser.add_argument("--fresh-all-inventory", type=Path, required=True)
    parser.add_argument(
        "--credential-dir", type=Path, default=default_credential_dir()
    )
    parser.add_argument("--max-messages", type=int, default=200)
    parser.add_argument("--max-history-pages", type=int, default=100)
    args = parser.parse_args()

    try:
        candidate = _load_candidate(args.candidate_evidence)
        if candidate.get("account_id") != args.account_id:
            raise SourceCaptureError("Candidate account differs from requested account")
        new_ids, inventory = read_source_ids(args.fresh_all_inventory)
        if inventory.get("account_id") != args.account_id:
            raise SourceCaptureError("Independent source inventory account missing/mismatched")
        if int(inventory["history_start"]) < int(candidate["source_history_id"]):
            raise SourceCaptureError("Fresh inventory predates frozen source frontier")
        provider = build_provider(Path("credentials.json"), args.credential_dir)
        account, credentials = provider.load(args.account_id)
        if account != args.account_id:
            raise SourceCaptureError("Gmail credentials account mismatch")
        report = audit_membership(
            GmailApiClient(credentials=credentials),
            candidate=candidate,
            current_ids=new_ids,
            current_history_id=inventory["history_end"],
            max_messages=args.max_messages,
            max_history_pages=args.max_history_pages,
        )
    except (OSError, ValueError, RuntimeError, TypeError, GmailApiError, GmailAuthError) as exc:
        print("RC8 LIST/GET AUDIT FAILED: exception_type=" + type(exc).__name__)
        if isinstance(exc, GmailHistoryExpiredError):
            print("reason: Gmail History cursor expired; no new snapshot initiated")
        return 2

    print(json.dumps(report, sort_keys=True, indent=2))
    return 0 if report["status"].startswith("SOURCE_COHORT_CONSISTENT") else 3


if __name__ == "__main__":
    raise SystemExit(main())
