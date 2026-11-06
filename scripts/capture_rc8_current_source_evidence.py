#!/usr/bin/env python3
"""Capture fresh source FULL RAW for only RC8 reconciliation exceptions.

Captures a small bounded cohort in a new immutable provenance namespace.
No Gmail writes. Never reclassifies a historical event as processed RAW.
Fails closed if relevant source History changed after the source inventory
frontier. Source IDs and message content never appear on stdout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from observe_current_source_inventory import write_private_inventory
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.api import GmailApiClient, GmailApiError, GmailHistoryExpiredError
from gmail_ingestion.auth import GmailAuthError, build_provider
from gmail_ingestion.config import default_credential_dir
from gmail_ingestion.persistence import RawMessageWriter


class SourceCaptureError(RuntimeError):
    """Provenance or boundary check prevented use of live recovery data."""


class SourceClient(Protocol):
    @property
    def messages(self) -> Any: ...

    @property
    def history(self) -> Any: ...


def _load_candidate(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise SourceCaptureError("Candidate evidence must be a JSON object")
    required = data.get("private_recovery_required_ids")
    source_history = data.get("source_history_id")
    if (
        data.get("schema_version") != 1
        or data.get("candidate_mode") != "CURRENT_RECONCILIATION"
        or data.get("status") != "BLOCKED_SOURCE_QUALIFICATION_REQUIRED"
        or not isinstance(required, list)
        or any(not isinstance(mid, str) or not mid for mid in required)
        or len(required) != len(set(required))
        or not isinstance(source_history, str) or not source_history.isdecimal()
    ):
        raise SourceCaptureError("RC8 candidate is incomplete or unsupported")
    return data


def _history_record_ids(record: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for field in (
        "messages", "messagesAdded", "messagesDeleted",
        "labelsAdded", "labelsRemoved",
    ):
        values = record.get(field) or []
        if not isinstance(values, list):
            raise SourceCaptureError("History mutation entries are malformed")
        for row in values:
            if not isinstance(row, Mapping):
                raise SourceCaptureError("History mutation row is malformed")
            nested = row.get("message") if field != "messages" else row
            if not isinstance(nested, Mapping):
                raise SourceCaptureError("History row lacks message reference")
            mid = nested.get("id")
            if not isinstance(mid, str) or not mid:
                raise SourceCaptureError("History row lacks source identifier")
            ids.add(mid)
    return ids


def _assert_stable_recovery_ids(
    client: SourceClient, *, since: str, ids: set[str],
    max_pages: int, page_size: int,
) -> str:
    token: str | None = None
    seen_tokens: set[str] = set()
    end = int(since)
    start = int(since)
    for _ in range(max_pages):
        page = client.history.list_history(
            start_history_id=since, page_token=token, max_results=page_size
        )
        history_id = page.get("historyId")
        if not isinstance(history_id, str) or not history_id.isdecimal():
            raise SourceCaptureError("History response has invalid frontier")
        if int(history_id) < start:
            raise SourceCaptureError("History frontier regressed")
        end = max(end, int(history_id))
        records = page.get("history") or []
        if not isinstance(records, list):
            raise SourceCaptureError("History records are malformed")
        for record in records:
            if not isinstance(record, Mapping):
                raise SourceCaptureError("History record is malformed")
            record_id = record.get("id")
            if not isinstance(record_id, str) or not record_id.isdecimal():
                raise SourceCaptureError("History event frontier is invalid")
            if not start < int(record_id) <= end:
                raise SourceCaptureError("History event outside requested cursor")
            if _history_record_ids(record) & ids:
                raise SourceCaptureError(
                    "A recovery message changed since the source inventory frontier"
                )
        nxt = page.get("nextPageToken")
        if nxt is None:
            return str(end)
        if not isinstance(nxt, str) or not nxt or nxt in seen_tokens:
            raise SourceCaptureError("History pagination is invalid")
        seen_tokens.add(nxt)
        token = nxt
    raise SourceCaptureError("History pagination cap reached")


def capture(
    client: SourceClient,
    *,
    store: LocalArtifactStore,
    candidate: Mapping[str, Any],
    run_id: UUID,
    max_messages: int = 200,
    max_payload_mib: int = 256,
    max_history_pages: int = 100,
) -> dict[str, object]:
    """Keep captured FULL evidence separate from original event RAW."""
    required = candidate.get("private_recovery_required_ids")
    source_frontier = candidate.get("source_history_id")
    if (
        not isinstance(required, list)
        or any(not isinstance(mid, str) or not mid for mid in required)
        or len(required) != len(set(required))
        or not isinstance(source_frontier, str) or not source_frontier.isdecimal()
        or not isinstance(candidate.get("candidate_id"), str)
    ):
        raise SourceCaptureError("Candidate recovery identity is invalid")
    if (
        not 0 <= len(required) <= max_messages or max_messages < 1
        or max_payload_mib < 1 or max_history_pages < 1
    ):
        raise SourceCaptureError("Recovery capture limits exceeded")
    namespace = "gmail.rc8_source_evidence"
    writer = RawMessageWriter(store, job_id=namespace)
    root = store.run_root(namespace, run_id)
    if (root / "source_manifest.json").exists():
        raise SourceCaptureError("Recovery run already has an immutable final manifest")

    payloads: dict[str, Mapping[str, Any]] = {}
    total_bytes = 0
    for mid in sorted(required):
        payload = client.messages.get_message(mid, format="full")
        labels = payload.get("labelIds")
        history = payload.get("historyId")
        if (
            payload.get("id") != mid
            or not isinstance(payload.get("threadId"), str)
            or not isinstance(payload.get("payload"), Mapping)
            or not isinstance(labels, list)
            or any(not isinstance(label, str) for label in labels)
            or "SPAM" in labels or "TRASH" in labels
            or not isinstance(history, str) or not history.isdecimal()
            or int(history) > int(source_frontier)
        ):
            raise SourceCaptureError("Recovered current message failed source identity/scope")
        total_bytes += len(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        if total_bytes > max_payload_mib * 1024 * 1024:
            raise SourceCaptureError("Source recovery payload budget exceeded")
        payloads[mid] = payload

    checked_through = _assert_stable_recovery_ids(
        client, since=source_frontier, ids=set(required),
        max_pages=max_history_pages, page_size=500,
    )
    for mid, payload in sorted(payloads.items()):
        if writer.exists(run_id, mid):
            if dict(writer.read(run_id, mid)) != dict(payload):
                raise SourceCaptureError("Existing immutable source evidence differs")
        else:
            writer.write(run_id, mid, payload)
    manifest: dict[str, object] = {
        "schema_version": 1,
        "status": "COMPLETED",
        "job_id": namespace,
        "run_id": str(run_id),
        "candidate_id": candidate["candidate_id"],
        "account_id": candidate.get("account_id"),
        "source_history_id": source_frontier,
        "history_checked_through": checked_through,
        "source_id_set_sha256": candidate.get("source_id_set_sha256"),
        "exception_ledger_sha256": candidate.get("exception_ledger_sha256"),
        "recovery_message_count": len(payloads),
        "recovery_message_ids": sorted(payloads),
        "content_sha256": {
            mid: hashlib.sha256(
                json.dumps(payload, ensure_ascii=False, sort_keys=True)
                .encode("utf-8")
            ).hexdigest()
            for mid, payload in sorted(payloads.items())
        },
        "source_messages_modified_via_api": False,
        "historical_raw_overwritten": False,
        "stable_release_qualified": False,
    }
    write_private_inventory(root / "source_manifest.json", manifest)
    return {
        "status": "RECOVERY_EVIDENCE_CAPTURED_NOT_RELEASE_QUALIFIED",
        "run_id": str(run_id),
        "candidate_id": candidate["candidate_id"],
        "recovery_message_count": len(payloads),
        "payload_mib": round(total_bytes / 1024**2, 2),
        "source_history_id": source_frontier,
        "history_checked_through": checked_through,
        "gmail_readonly": True,
        "historical_raw_overwritten": False,
        "current_pointer_promoted": False,
        "stable_release_qualified": False,
    }


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--account-id", required=True)
    p.add_argument("--candidate-evidence", type=Path, required=True)
    p.add_argument("--artifact-root", type=Path, required=True)
    p.add_argument("--run-id", type=UUID, required=True)
    p.add_argument("--credential-dir", type=Path, default=default_credential_dir())
    p.add_argument("--max-messages", type=int, default=200)
    p.add_argument("--max-payload-mib", type=int, default=256)
    a = p.parse_args()
    try:
        candidate = _load_candidate(a.candidate_evidence)
        if candidate.get("account_id") != a.account_id:
            raise SourceCaptureError("Requested account differs from candidate")
        provider = build_provider(Path("credentials.json"), a.credential_dir)
        resolved, credentials = provider.load(a.account_id)
        if resolved != a.account_id:
            raise SourceCaptureError("Authenticated account differs from candidate")
        result = capture(
            GmailApiClient(credentials=credentials),
            store=LocalArtifactStore(a.artifact_root),
            candidate=candidate,
            run_id=a.run_id,
            max_messages=a.max_messages,
            max_payload_mib=a.max_payload_mib,
        )
    except (OSError, ValueError, RuntimeError, TypeError, GmailApiError, GmailAuthError) as exc:
        print("RC8 SOURCE CAPTURE FAILED: exception_type=" + type(exc).__name__)
        if isinstance(exc, GmailHistoryExpiredError):
            print("reason: History expired; no full snapshot initiated")
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
