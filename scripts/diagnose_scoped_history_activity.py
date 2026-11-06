#!/usr/bin/env python3
"""Aggregate Gmail History activity without enumerating the mailbox.

This diagnostic uses gmail.readonly, never downloads message bodies, never
writes evidence, and never exposes message IDs, addresses or labels outside
the SPAM/TRASH scope classification. It is not a release qualification.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from gmail_ingestion.api import GmailApiClient, GmailApiError, GmailHistoryExpiredError
from gmail_ingestion.auth import GmailAuthError, build_provider
from gmail_ingestion.config import default_credential_dir


class HistoryActivityIncomplete(RuntimeError):
    """Incomplete or malformed source History response."""


class HistoryReader(Protocol):
    def list_history(
        self, *, start_history_id: str, page_token: str | None = None,
        max_results: int = 500,
    ) -> Mapping[str, Any]: ...


def diagnose_history(
    history: HistoryReader, *, start_history_id: str,
    max_pages: int = 50, page_size: int = 500,
) -> dict[str, object]:
    """Summarize all returned History pages, with a fail-closed page bound."""
    if not start_history_id.isdecimal() or not start_history_id:
        raise ValueError("Numeric start History checkpoint required")
    if not 1 <= max_pages <= 100000 or not 1 <= page_size <= 500:
        raise ValueError("Invalid History pagination limits")

    counts: Counter[str] = Counter()
    pages = 0
    seen_tokens: set[str] = set()
    page_token: str | None = None
    end = int(start_history_id)

    for _ in range(max_pages):
        response = history.list_history(
            start_history_id=start_history_id,
            page_token=page_token,
            max_results=page_size,
        )
        pages += 1
        frontier = response.get("historyId")
        if not isinstance(frontier, str) or not frontier.isdecimal():
            raise HistoryActivityIncomplete("Malformed History response frontier")
        if int(frontier) < int(start_history_id):
            raise HistoryActivityIncomplete("History response predates start")
        end = max(end, int(frontier))
        records = response.get("history") or []
        if not isinstance(records, list):
            raise HistoryActivityIncomplete("Malformed History records")
        for record in records:
            if not isinstance(record, Mapping):
                raise HistoryActivityIncomplete("Malformed History record")
            record_id = record.get("id")
            if not isinstance(record_id, str) or not record_id.isdecimal():
                raise HistoryActivityIncomplete("Malformed History record ID")
            if not int(start_history_id) < int(record_id) <= end:
                raise HistoryActivityIncomplete("History record outside frontier")
            counts["history_records"] += 1
            for field, key in (
                ("messagesAdded", "message_add_entries"),
                ("messagesDeleted", "message_delete_entries"),
            ):
                changes = record.get(field, [])
                if not isinstance(changes, list):
                    raise HistoryActivityIncomplete("Malformed message changes")
                for change in changes:
                    if not isinstance(change, Mapping):
                        raise HistoryActivityIncomplete("Malformed message change")
                    counts[key] += 1
            for field, key in (
                ("labelsAdded", "label_add_entries"),
                ("labelsRemoved", "label_remove_entries"),
            ):
                changes = record.get(field, [])
                if not isinstance(changes, list):
                    raise HistoryActivityIncomplete("Malformed label changes")
                for change in changes:
                    if not isinstance(change, Mapping):
                        raise HistoryActivityIncomplete("Malformed label change")
                    labels = change.get("labelIds")
                    if not isinstance(labels, list) or not all(
                        isinstance(label, str) for label in labels
                    ):
                        raise HistoryActivityIncomplete("Malformed label IDs")
                    counts[key] += 1
                    if "SPAM" in labels or "TRASH" in labels:
                        counts["scope_membership_label_changes"] += 1

        next_token = response.get("nextPageToken")
        if next_token is None:
            return {
                "status": "COMPLETE_DIAGNOSTIC_ONLY",
                "start_history_id": start_history_id,
                "end_history_id": str(end),
                "history_pages": pages,
                "history_records": counts["history_records"],
                "message_add_entries": counts["message_add_entries"],
                "message_delete_entries": counts["message_delete_entries"],
                "label_add_entries": counts["label_add_entries"],
                "label_remove_entries": counts["label_remove_entries"],
                "scope_membership_label_changes":
                    counts["scope_membership_label_changes"],
                "would_block_excluded_scope_if_during_scan": (
                    counts["message_add_entries"] > 0
                    or counts["scope_membership_label_changes"] > 0
                ),
                "mailbox_inventory_scanned": False,
                "gmail_bodies_downloaded": 0,
                "persistent_writes": 0,
                "release_qualified": False,
            }
        if (
            not isinstance(next_token, str)
            or not next_token
            or next_token in seen_tokens
        ):
            raise HistoryActivityIncomplete("Malformed or repeated History page token")
        seen_tokens.add(next_token)
        page_token = next_token
    raise HistoryActivityIncomplete("History page cap reached")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--start-history-id", required=True)
    parser.add_argument("--credential-dir", type=Path, default=default_credential_dir())
    parser.add_argument("--max-pages", type=int, default=50)
    parser.add_argument("--page-size", type=int, default=500)
    args = parser.parse_args()

    try:
        provider = build_provider(Path("credentials.json"), args.credential_dir)
        resolved, credentials = provider.load(args.account_id)
        if resolved != args.account_id:
            raise ValueError("Authenticated account identity mismatch")
        report = diagnose_history(
            GmailApiClient(credentials=credentials).history,
            start_history_id=args.start_history_id,
            max_pages=args.max_pages,
            page_size=args.page_size,
        )
    except HistoryActivityIncomplete:
        print("HISTORY ACTIVITY INCOMPLETE: source pagination/validation failed")
        return 3
    except (OSError, ValueError, RuntimeError, TypeError, GmailApiError, GmailAuthError) as exc:
        print("HISTORY ACTIVITY FAILED: exception_type=" + type(exc).__name__)
        if isinstance(exc, GmailHistoryExpiredError):
            print("reason: History cursor expired; no automatic snapshot was started")
        return 2
    print(json.dumps(report, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
