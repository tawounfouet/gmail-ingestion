"""Persistent, deduplicated discovery queue for full Gmail snapshots."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pyingestkit.artifacts.base import ArtifactStore
from pyingestkit.core.exceptions import StorageError

from gmail_ingestion.domain import MessageDiscoveryRecord, MessageFetchStatus

_CONTROL_PATH = "control/message_discovery.json"
_JOB_ID = "gmail.messages"


class MessageDiscoveryError(RuntimeError):
    """Raised when persisted discovery state is invalid or inconsistent."""


class MessageDiscoveryQueue:
    """Durable discovery/fetch state for one account and snapshot run."""

    def __init__(
        self,
        store: ArtifactStore,
        run_id: UUID,
        account_id: str,
        history_start: str | None,
        *,
        records: dict[str, MessageDiscoveryRecord] | None = None,
        discovery_complete: bool = False,
        pages_scanned: int = 0,
        loaded_existing: bool = False,
        next_page_token: str | None = None,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.account_id = account_id
        self.history_start = history_start
        self.records = records or {}
        self.discovery_complete = discovery_complete
        self.pages_scanned = pages_scanned
        self.loaded_existing = loaded_existing
        self.next_page_token = next_page_token

    @classmethod
    def load_or_create(
        cls,
        store: ArtifactStore,
        run_id: UUID,
        account_id: str,
        history_start: str | None,
    ) -> MessageDiscoveryQueue:
        """Load prior control state or start a new queue."""
        try:
            raw = store.read_bytes(store.uri_for(_JOB_ID, run_id, _CONTROL_PATH))
        except StorageError:
            return cls(store, run_id, account_id, history_start)

        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MessageDiscoveryError("Persisted message discovery state is invalid JSON") from exc
        if not isinstance(payload, dict):
            raise MessageDiscoveryError("Persisted message discovery state must be a JSON object")

        stored_run_id = str(payload.get("run_id", ""))
        stored_account_id = str(payload.get("account_id", ""))
        if stored_run_id != str(run_id):
            raise MessageDiscoveryError("Persisted discovery run_id does not match requested run")
        if stored_account_id != account_id:
            raise MessageDiscoveryError("Persisted discovery account_id does not match requested account")

        records_payload = payload.get("records", [])
        if not isinstance(records_payload, list):
            raise MessageDiscoveryError("Persisted discovery records must be a list")

        records: dict[str, MessageDiscoveryRecord] = {}
        for item in records_payload:
            if not isinstance(item, Mapping):
                raise MessageDiscoveryError("Persisted discovery record must be an object")
            record = _record_from_payload(item)
            if record.message_id in records:
                raise MessageDiscoveryError("Persisted discovery state contains duplicate message IDs")
            records[record.message_id] = record

        return cls(
            store,
            run_id,
            account_id,
            _optional_str(payload.get("history_start")),
            records=records,
            discovery_complete=bool(payload.get("discovery_complete", False)),
            pages_scanned=_non_negative_int(payload.get("pages_scanned", 0), "pages_scanned"),
            loaded_existing=True,
            next_page_token=_optional_str(payload.get("next_page_token")),
        )

    def merge_page(
        self,
        message_refs: Sequence[Mapping[str, Any]],
        *,
        page_index: int,
        discovered_at: datetime | None = None,
    ) -> int:
        """Add newly discovered message IDs without duplicating prior entries."""
        timestamp = discovered_at or datetime.now(UTC)
        added = 0
        for ref in message_refs:
            message_id = _required_str(ref.get("id"), "message id")
            thread_id = _required_str(ref.get("threadId"), "thread id")
            if message_id in self.records:
                continue
            self.records[message_id] = MessageDiscoveryRecord(
                run_id=str(self.run_id),
                account_id=self.account_id,
                message_id=message_id,
                thread_id=thread_id,
                page_index=page_index,
                discovered_at=timestamp,
            )
            added += 1
        self.pages_scanned = max(self.pages_scanned, page_index + 1)
        return added

    def checkpoint_discovery_page(self, next_page_token: str | None) -> None:
        """Persist the exact cursor required to continue after the current page."""
        self.next_page_token = next_page_token
        self.discovery_complete = next_page_token is None

    def mark_discovery_complete(self) -> None:
        self.next_page_token = None
        self.discovery_complete = True

    def iter_pending(self):
        """Yield records that still need a confirmed immutable RAW message."""
        return (
            record
            for record in self.records.values()
            if record.fetch_status is not MessageFetchStatus.FETCHED
        )

    def pending(self) -> tuple[MessageDiscoveryRecord, ...]:
        """Return pending records as a tuple for compatibility."""
        return tuple(self.iter_pending())

    def mark_fetched(self, message_id: str, *, attempts: int) -> None:
        record = self._record(message_id)
        self.records[message_id] = replace(
            record,
            fetch_status=MessageFetchStatus.FETCHED,
            fetch_attempts=record.fetch_attempts + attempts,
            last_fetch_at=datetime.now(UTC),
            error_code=None,
            error_message=None,
        )

    def mark_failed(
        self,
        message_id: str,
        *,
        attempts: int,
        error_code: str,
        error_message: str,
    ) -> None:
        record = self._record(message_id)
        self.records[message_id] = replace(
            record,
            fetch_status=MessageFetchStatus.FAILED,
            fetch_attempts=record.fetch_attempts + attempts,
            last_fetch_at=datetime.now(UTC),
            error_code=error_code,
            error_message=error_message,
        )

    @property
    def fetched_count(self) -> int:
        return sum(
            record.fetch_status is MessageFetchStatus.FETCHED
            for record in self.records.values()
        )

    @property
    def failed_count(self) -> int:
        return sum(
            record.fetch_status is MessageFetchStatus.FAILED
            for record in self.records.values()
        )

    def persist(self) -> None:
        """Atomically persist mutable control state through PyIngestKit."""
        self.store.write_json(_JOB_ID, self.run_id, _CONTROL_PATH, self.to_payload())

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "run_id": str(self.run_id),
            "account_id": self.account_id,
            "history_start": self.history_start,
            "discovery_complete": self.discovery_complete,
            "pages_scanned": self.pages_scanned,
            "next_page_token": self.next_page_token,
            "records": [_record_to_payload(record) for record in self.records.values()],
        }

    def _record(self, message_id: str) -> MessageDiscoveryRecord:
        try:
            return self.records[message_id]
        except KeyError as exc:
            raise MessageDiscoveryError(f"Unknown discovered message {message_id!r}") from exc


def _record_to_payload(record: MessageDiscoveryRecord) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "account_id": record.account_id,
        "message_id": record.message_id,
        "thread_id": record.thread_id,
        "page_index": record.page_index,
        "discovered_at": record.discovered_at.isoformat(),
        "fetch_status": record.fetch_status.value,
        "fetch_attempts": record.fetch_attempts,
        "last_fetch_at": (
            record.last_fetch_at.isoformat() if record.last_fetch_at is not None else None
        ),
        "error_code": record.error_code,
        "error_message": record.error_message,
    }


def _record_from_payload(payload: Mapping[str, Any]) -> MessageDiscoveryRecord:
    try:
        fetch_status = MessageFetchStatus(str(payload.get("fetch_status", "PENDING")))
    except ValueError as exc:
        raise MessageDiscoveryError("Unknown persisted message fetch status") from exc
    return MessageDiscoveryRecord(
        run_id=_required_str(payload.get("run_id"), "run_id"),
        account_id=_required_str(payload.get("account_id"), "account_id"),
        message_id=_required_str(payload.get("message_id"), "message_id"),
        thread_id=_required_str(payload.get("thread_id"), "thread_id"),
        page_index=_non_negative_int(payload.get("page_index"), "page_index"),
        discovered_at=_datetime(payload.get("discovered_at"), "discovered_at"),
        fetch_status=fetch_status,
        fetch_attempts=_non_negative_int(payload.get("fetch_attempts", 0), "fetch_attempts"),
        last_fetch_at=_optional_datetime(payload.get("last_fetch_at"), "last_fetch_at"),
        error_code=_optional_str(payload.get("error_code")),
        error_message=_optional_str(payload.get("error_message")),
    )


def _required_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise MessageDiscoveryError(f"Missing or invalid {name}")
    return value


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise MessageDiscoveryError(f"{name} must be a non-negative integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise MessageDiscoveryError(f"{name} must be a non-negative integer") from exc
    if result < 0:
        raise MessageDiscoveryError(f"{name} must be a non-negative integer")
    return result


def _datetime(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise MessageDiscoveryError(f"{name} must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MessageDiscoveryError(f"{name} must be an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise MessageDiscoveryError(f"{name} must include a timezone")
    return parsed


def _optional_datetime(value: Any, name: str) -> datetime | None:
    if value is None:
        return None
    return _datetime(value, name)
