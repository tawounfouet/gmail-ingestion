#!/usr/bin/env python3
"""Derive a Gmail source inventory excluding Spam/Trash from saved all-mail IDs.

Uses the prior private immutable all-mail inventory, a bounded History catchup,
and fresh SPAM/TRASH label-specific ID lists. No message bodies or full mailbox
re-scan. A History change during the label-list observation fails closed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from compare_source_inventory_ids import read_source_ids
from observe_current_source_inventory import write_private_inventory

from gmail_ingestion.api import GmailApiClient, GmailApiError, GmailHistoryExpiredError
from gmail_ingestion.auth import GmailAuthError, build_provider
from gmail_ingestion.config import default_credential_dir


class SourceReconciliationError(RuntimeError):
    """A read-only observation was incomplete or cannot be aligned safely."""


class SourceClient(Protocol):
    def get_profile(self) -> Mapping[str, Any]: ...

    @property
    def messages(self) -> Any: ...

    @property
    def history(self) -> Any: ...


def _history(value: object) -> int:
    if not isinstance(value, str) or not value.isdecimal():
        raise SourceReconciliationError("Source frontier is missing or malformed")
    return int(value)


def _records(client: SourceClient, start: int, *, max_pages: int, page_size: int) -> list[Mapping[str, Any]]:
    """List all History records since an explicit cursor with cycle/page guards."""
    token: str | None = None
    seen_tokens: set[str] = set()
    records: list[Mapping[str, Any]] = []
    previous_history_id = start
    for _ in range(max_pages):
        response = client.history.list_history(
            start_history_id=str(start), page_token=token, max_results=page_size
        )
        if _history(response.get("historyId")) < start:
            raise SourceReconciliationError("History response regressed")
        batch = response.get("history") or []
        if not isinstance(batch, list):
            raise SourceReconciliationError("History records are malformed")
        for row in batch:
            if not isinstance(row, Mapping):
                raise SourceReconciliationError("History row is malformed")
            record_id = _history(row.get("id"))
            if record_id <= start or record_id < previous_history_id:
                raise SourceReconciliationError("History row frontier is not ordered")
            previous_history_id = record_id
            records.append(row)
        nxt = response.get("nextPageToken")
        if nxt is None:
            return records
        if not isinstance(nxt, str) or not nxt or nxt in seen_tokens:
            raise SourceReconciliationError("History pagination is invalid or repeated")
        seen_tokens.add(nxt)
        token = nxt
    raise SourceReconciliationError("History page cap reached")


def _changes(record: Mapping[str, Any], field: str) -> list[Mapping[str, Any]]:
    value = record.get(field) or []
    if not isinstance(value, list):
        raise SourceReconciliationError("History mutations are malformed")
    if not all(isinstance(row, Mapping) for row in value):
        raise SourceReconciliationError("History mutation is malformed")
    return value


def _source_id(change: Mapping[str, Any]) -> str:
    message = change.get("message")
    if not isinstance(message, Mapping):
        raise SourceReconciliationError("History mutation has no message")
    identifier = message.get("id")
    if not isinstance(identifier, str) or not identifier:
        raise SourceReconciliationError("History mutation has invalid message ID")
    return identifier


def _read_label_ids(
    client: SourceClient, label: str, *, max_pages: int, page_size: int
) -> tuple[set[str], int]:
    token: str | None = None
    seen_tokens: set[str] = set()
    ids: set[str] = set()
    for page_number in range(1, max_pages + 1):
        result = client.messages.list_messages(
            page_token=token, max_results=page_size,
            label_ids=(label,), include_spam_trash=True
        )
        messages = result.get("messages") or []
        if not isinstance(messages, list):
            raise SourceReconciliationError("Label inventory response is malformed")
        for item in messages:
            if not isinstance(item, Mapping):
                raise SourceReconciliationError("Label inventory item is malformed")
            mid = item.get("id")
            if not isinstance(mid, str) or not mid:
                raise SourceReconciliationError("Label inventory item lacks ID")
            ids.add(mid)
        nxt = result.get("nextPageToken")
        if nxt is None:
            return ids, page_number
        if not isinstance(nxt, str) or not nxt or nxt in seen_tokens:
            raise SourceReconciliationError("Label pagination is invalid or repeated")
        seen_tokens.add(nxt)
        token = nxt
    raise SourceReconciliationError("Label inventory page cap reached")


def _verify_quiet_label_window(
    records: list[Mapping[str, Any]], start: int, end: int,
) -> tuple[int, int]:
    """Reject any source-scope change while Spam/Trash sets are enumerated."""
    considered = 0
    scope_changes = 0
    for record in records:
        record_id = _history(record.get("id"))
        if not start < record_id <= end:
            continue
        considered += 1
        for field in ("messagesAdded", "messagesDeleted"):
            if _changes(record, field):
                scope_changes += 1
        for field in ("labelsAdded", "labelsRemoved"):
            for change in _changes(record, field):
                labels = change.get("labelIds")
                if not isinstance(labels, list) or any(
                    not isinstance(x, str) for x in labels
                ):
                    raise SourceReconciliationError("History labels are malformed")
                if "SPAM" in labels or "TRASH" in labels:
                    scope_changes += 1
    if scope_changes:
        raise SourceReconciliationError(
            "Source-scope changes occurred during Spam/Trash label observation"
        )
    return considered, scope_changes


def _apply_membership(
    original: set[str], records: list[Mapping[str, Any]], start: int, end: int
) -> tuple[set[str], int, int]:
    """Advance the *all-mail* ID inventory, never synthesizing a deletion."""
    ids = set(original)
    added = deleted = 0
    for record in records:
        history_id = _history(record.get("id"))
        if not start < history_id <= end:
            continue
        add_ids = {_source_id(change) for change in _changes(record, "messagesAdded")}
        delete_ids = {_source_id(change) for change in _changes(record, "messagesDeleted")}
        if add_ids.intersection(delete_ids):
            raise SourceReconciliationError(
                "Ambiguous add/delete at the same Gmail History record"
            )
        ids.update(add_ids)
        ids.difference_update(delete_ids)
        added += len(add_ids)
        deleted += len(delete_ids)
    return ids, added, deleted


def derive_inventory(
    client: SourceClient,
    *,
    previous_inventory: Path,
    max_history_pages: int = 200,
    max_label_pages: int = 1000,
    page_size: int = 500,
) -> dict[str, object]:
    """Build one private current-scope ID set from a previously captured all-mail set."""
    if not 1 <= page_size <= 500 or max_history_pages < 1 or max_label_pages < 1:
        raise ValueError("Positive page caps and page_size 1..500 required")
    previous_ids, prior = read_source_ids(previous_inventory)
    prior_frontier = _history(prior["history_end"])
    label_start = _history(client.get_profile().get("historyId"))
    if label_start < prior_frontier:
        raise SourceReconciliationError("Source frontier regressed behind saved inventory")

    spam_ids, spam_pages = _read_label_ids(
        client, "SPAM", max_pages=max_label_pages, page_size=page_size
    )
    trash_ids, trash_pages = _read_label_ids(
        client, "TRASH", max_pages=max_label_pages, page_size=page_size
    )
    label_end = _history(client.get_profile().get("historyId"))
    if label_end < label_start:
        raise SourceReconciliationError("Source frontier regressed during label enumeration")

    # Explicitly verify the listing window before accepting the label ID sets.
    after_start = _records(
        client, label_start, max_pages=max_history_pages, page_size=page_size
    )
    quiet_records, quiet_changes = _verify_quiet_label_window(
        after_start, label_start, label_end
    )
    # Rebuild all-mail membership at exactly the new source frontier. A fresh
    # History listing is used because the previously captured inventory is at
    # an earlier frontier; the result is NOT a new full snapshot.
    since_prior = _records(
        client, prior_frontier, max_pages=max_history_pages, page_size=page_size
    )
    all_ids, adds, deletes = _apply_membership(
        previous_ids, since_prior, prior_frontier, label_end
    )
    if not spam_ids.issubset(all_ids) or not trash_ids.issubset(all_ids):
        raise SourceReconciliationError(
            "Label inventories contain IDs absent from the aligned all-mail set"
        )
    scoped = sorted(all_ids - spam_ids - trash_ids)
    # Both source and prior inventory hashes are included in the private
    # evidence. Public stdout removes the message IDs.
    digest = hashlib.sha256(("\n".join(scoped) + "\n").encode("utf-8")).hexdigest()
    return {
        "schema_version": 1,
        "status": "OBSERVED_NOT_RELEASE_QUALIFIED",
        "scope": "include_spam_trash=false; unfiltered Gmail messages",
        "include_spam_trash": False,
        "history_start": str(label_start),
        "history_end": str(label_end),
        "ids": scoped,
        "id_set_sha256": digest,
        "message_count": len(scoped),
        "source_all_inventory_history_id": str(prior_frontier),
        "source_all_inventory_sha256": prior["id_set_sha256"],
        "all_message_count_at_target": len(all_ids),
        "spam_message_count": len(spam_ids),
        "trash_message_count": len(trash_ids),
        "spam_pages": spam_pages,
        "trash_pages": trash_pages,
        "history_add_entries_replayed": adds,
        "history_delete_entries_replayed": deletes,
        "quiet_window_history_records": quiet_records,
        "quiet_window_scope_changes": quiet_changes,
        "history_catchup_complete": True,
        "source_scope_evidence_version": 1,
        "current_projection_compared": False,
        "gmail_bodies_downloaded": 0,
        "current_state_qualified": False,
        "stable_release_qualified": False,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--account-id", required=True)
    p.add_argument("--previous-all-inventory", type=Path, required=True)
    p.add_argument("--evidence-out", type=Path, required=True)
    p.add_argument("--credential-dir", type=Path, default=default_credential_dir())
    p.add_argument("--max-history-pages", type=int, default=200)
    p.add_argument("--max-label-pages", type=int, default=1000)
    p.add_argument("--page-size", type=int, default=500)
    a = p.parse_args()
    try:
        provider = build_provider(Path("credentials.json"), a.credential_dir)
        resolved, credentials = provider.load(a.account_id)
        if resolved != a.account_id:
            raise SourceReconciliationError("Account provenance mismatch")
        report = derive_inventory(
            GmailApiClient(credentials=credentials),
            previous_inventory=a.previous_all_inventory,
            max_history_pages=a.max_history_pages,
            max_label_pages=a.max_label_pages,
            page_size=a.page_size,
        )
        write_private_inventory(a.evidence_out, report)
    except (OSError, ValueError, RuntimeError, GmailApiError, GmailAuthError) as exc:
        print("SCOPED INVENTORY FAILED: exception_type=" + type(exc).__name__)
        if isinstance(exc, GmailHistoryExpiredError):
            print("reason: Gmail History cursor expired; no snapshot started")
        print("new_inventory_committed: false")
        return 2
    public = {key: value for key, value in report.items() if key != "ids"}
    print(json.dumps(public, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
