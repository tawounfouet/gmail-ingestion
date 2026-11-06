#!/usr/bin/env python3
"""Read-only current Gmail ID inventory with a bounded History catchup.

This is independent source evidence for later Current reconciliation, NOT a
stable-release GO. The private inventory contains Gmail IDs: never commit it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from gmail_ingestion.api import GmailApiClient, GmailApiError, GmailHistoryExpiredError
from gmail_ingestion.auth import GmailAuthError, build_provider
from gmail_ingestion.config import default_credential_dir


class InventoryIncompleteError(RuntimeError):
    """No inventory may be accepted when pagination or History is incomplete."""


class SourceClient(Protocol):
    def get_profile(self) -> Mapping[str, Any]: ...

    @property
    def messages(self) -> Any: ...

    @property
    def history(self) -> Any: ...


def _numeric_history(value: object) -> str:
    if not isinstance(value, str) or not value.isdecimal():
        raise InventoryIncompleteError("Source historyId is missing or malformed")
    return value


def _source_id(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise InventoryIncompleteError("Source message ID is missing or invalid")
    return value


def _page_token(value: object, seen: set[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value or value in seen:
        raise InventoryIncompleteError("Source pagination token is invalid or repeated")
    seen.add(value)
    return value


def observe_inventory(
    client: SourceClient,
    *,
    max_message_pages: int = 1000,
    max_history_pages: int = 200,
    page_size: int = 500,
) -> dict[str, object]:
    """Enumerate source IDs, then converge them through History from start.

    Includes Spam/Trash explicitly so the two data sources have the same
    mailbox-wide scope. Caller must compare scope against the local baseline.
    No source payload, subject, sender, body, or label is downloaded.
    """
    if (
        not 1 <= max_message_pages <= 100000
        or not 1 <= max_history_pages <= 100000
        or not 1 <= page_size <= 500
    ):
        raise ValueError("Invalid inventory page bounds")

    start_history_id = _numeric_history(client.get_profile().get("historyId"))
    ids: set[str] = set()
    seen_tokens: set[str] = set()
    next_token: str | None = None
    message_pages = 0
    message_references = 0

    for _ in range(max_message_pages):
        response = client.messages.list_messages(
            page_token=next_token,
            max_results=page_size,
            include_spam_trash=True,
        )
        message_pages += 1
        items = response.get("messages", [])
        if not isinstance(items, list):
            raise InventoryIncompleteError("Source messages list is malformed")
        for item in items:
            if not isinstance(item, Mapping):
                raise InventoryIncompleteError("Source message reference is malformed")
            message_references += 1
            ids.add(_source_id(item.get("id")))
        next_token = _page_token(response.get("nextPageToken"), seen_tokens)
        if next_token is None:
            break
    else:
        raise InventoryIncompleteError("Message inventory page cap reached")

    history_pages = 0
    history_records = 0
    catchup_added = 0
    catchup_deleted = 0
    next_token = None
    seen_tokens = set()
    end_history_id = start_history_id

    for _ in range(max_history_pages):
        response = client.history.list_history(
            start_history_id=start_history_id,
            page_token=next_token,
            max_results=page_size,
        )
        history_pages += 1
        response_history_id = _numeric_history(response.get("historyId"))
        if int(response_history_id) < int(start_history_id):
            raise InventoryIncompleteError("History response predates listing start")
        if int(response_history_id) > int(end_history_id):
            end_history_id = response_history_id
        records = response.get("history", [])
        if not isinstance(records, list):
            raise InventoryIncompleteError("History records list is malformed")
        for record in records:
            if not isinstance(record, Mapping):
                raise InventoryIncompleteError("History record is malformed")
            record_history = _numeric_history(record.get("id"))
            if not int(start_history_id) < int(record_history) <= int(end_history_id):
                raise InventoryIncompleteError("History event outside observation range")
            history_records += 1
            for field, is_add in (("messagesAdded", True), ("messagesDeleted", False)):
                changes = record.get(field, [])
                if not isinstance(changes, list):
                    raise InventoryIncompleteError("History mutation collection is malformed")
                for change in changes:
                    if not isinstance(change, Mapping):
                        raise InventoryIncompleteError("History mutation is malformed")
                    message = change.get("message")
                    if not isinstance(message, Mapping):
                        raise InventoryIncompleteError("History mutation lacks source message")
                    message_id = _source_id(message.get("id"))
                    if is_add:
                        ids.add(message_id)
                        catchup_added += 1
                    else:
                        ids.discard(message_id)
                        catchup_deleted += 1
        next_token = _page_token(response.get("nextPageToken"), seen_tokens)
        if next_token is None:
            break
    else:
        raise InventoryIncompleteError("History catchup page cap reached")

    ordered = sorted(ids)
    digest = hashlib.sha256(
        ("\\n".join(ordered) + "\\n").encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": 1,
        "status": "OBSERVED_NOT_RELEASE_QUALIFIED",
        "scope": "include_spam_trash=true; unfiltered Gmail messages",
        "history_start": start_history_id,
        "history_end": end_history_id,
        "ids": ordered,
        "id_set_sha256": digest,
        "message_count": len(ordered),
        "message_pages": message_pages,
        "message_references_seen": message_references,
        "history_pages": history_pages,
        "history_records": history_records,
        "history_add_entries": catchup_added,
        "history_delete_entries": catchup_deleted,
        "history_catchup_complete": True,
        "gmail_bodies_downloaded": 0,
        "gmail_labels_verified": False,
        "current_projection_compared": False,
        "stable_release_qualified": False,
    }


def write_private_inventory(path: Path, inventory: Mapping[str, object]) -> None:
    """Create-once private evidence; refuse silent overwrite of prior observations."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as output:
        json.dump(inventory, output, sort_keys=True, indent=2)
        output.write("\\n")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Observe current source message IDs, then catch up History (read-only)."
    )
    parser.add_argument("--account-id", required=True)
    parser.add_argument("--credential-dir", type=Path, default=default_credential_dir())
    parser.add_argument("--evidence-out", type=Path, required=True)
    parser.add_argument("--max-message-pages", type=int, default=1000)
    parser.add_argument("--max-history-pages", type=int, default=200)
    parser.add_argument("--page-size", type=int, default=500)
    args = parser.parse_args()
    try:
        provider = build_provider(Path("credentials.json"), args.credential_dir)
        resolved, credentials = provider.load(args.account_id)
        if resolved != args.account_id:
            raise ValueError("Authenticated account identity mismatch")
        report = observe_inventory(
            GmailApiClient(credentials=credentials),
            max_message_pages=args.max_message_pages,
            max_history_pages=args.max_history_pages,
            page_size=args.page_size,
        )
        write_private_inventory(args.evidence_out, report)
    except (
        OSError,
        ValueError,
        RuntimeError,
        TypeError,
        GmailApiError,
        GmailAuthError,
    ) as exc:
        print("SOURCE INVENTORY FAILED: exception_type=" + type(exc).__name__)
        if isinstance(exc, GmailHistoryExpiredError):
            print("reason: History cursor expired; no Full Snapshot was started")
        return 2
    # Raw IDs exist exclusively in the private local evidence file, not stdout.
    public = {key: value for key, value in report.items() if key != "ids"}
    print(json.dumps(public, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
