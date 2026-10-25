"""Durable event receipts for cross-run Gmail History idempotence."""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

from gmail_ingestion.domain import GmailHistoryEvent

_ACCOUNT_ID_RE = re.compile(r"^acct_[0-9a-f]{24}$")
_RECEIPT_SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class HistoryEventReceipt:
    """Durable proof that one logical Gmail History event effect was applied."""

    account_id: str
    event_id: str
    history_id: str
    event_type: str
    message_id: str
    label_id: str | None
    source_run_id: UUID
    applied_at: datetime
    schema_version: str = _RECEIPT_SCHEMA_VERSION


class GmailHistoryReceiptStore(Protocol):
    """Control-plane idempotence contract for History event reduction."""

    def load(self, account_id: str, event_id: str) -> HistoryEventReceipt | None: ...

    def mark_applied(
        self,
        event: GmailHistoryEvent,
        *,
        applied_at: datetime | None = None,
    ) -> HistoryEventReceipt: ...


class LocalHistoryReceiptStore:
    """Atomic account-scoped filesystem receipt store shared by incremental runs."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser()
        self.accounts_dir = self.root / "accounts"
        self._ensure_private_dir(self.root)
        self._ensure_private_dir(self.accounts_dir)

    def load(self, account_id: str, event_id: str) -> HistoryEventReceipt | None:
        path = self._path(account_id, event_id)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Unable to read History event receipt {path}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"History event receipt {path} must contain an object")
        return _from_payload(payload)

    def mark_applied(
        self,
        event: GmailHistoryEvent,
        *,
        applied_at: datetime | None = None,
    ) -> HistoryEventReceipt:
        receipt = HistoryEventReceipt(
            account_id=event.account_id,
            event_id=event.event_id,
            history_id=event.history_id,
            event_type=event.event_type.value,
            message_id=event.message_id,
            label_id=event.label_id,
            source_run_id=event.run_id,
            applied_at=applied_at or datetime.now(UTC),
        )
        path = self._path(event.account_id, event.event_id)
        existing = self.load(event.account_id, event.event_id)
        if existing is not None:
            if (
                existing.account_id != receipt.account_id
                or existing.event_id != receipt.event_id
                or existing.history_id != receipt.history_id
                or existing.event_type != receipt.event_type
                or existing.message_id != receipt.message_id
                or existing.label_id != receipt.label_id
            ):
                raise ValueError(
                    f"History event receipt conflict for {event.event_id}"
                )
            return existing
        self._write_private_text(
            path,
            json.dumps(_to_payload(receipt), indent=2, sort_keys=True) + "\n",
        )
        return receipt

    def _path(self, account_id: str, event_id: str) -> Path:
        if not _ACCOUNT_ID_RE.fullmatch(account_id):
            raise ValueError("Invalid account_id format")
        if not event_id or any(char not in "0123456789abcdef" for char in event_id):
            raise ValueError("event_id must be a lowercase hexadecimal digest")
        directory = (
            self.accounts_dir
            / account_id
            / "history_receipts"
            / event_id[:2]
        )
        self._ensure_private_dir(directory)
        return directory / f"{event_id}.json"

    @staticmethod
    def _ensure_private_dir(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            path.chmod(0o700)
        except OSError:
            pass

    @staticmethod
    def _write_private_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        finally:
            if temp.exists():
                temp.unlink()


def _to_payload(receipt: HistoryEventReceipt) -> dict[str, str | None]:
    return {
        "receipt_schema_version": receipt.schema_version,
        "account_id": receipt.account_id,
        "event_id": receipt.event_id,
        "history_id": receipt.history_id,
        "event_type": receipt.event_type,
        "message_id": receipt.message_id,
        "label_id": receipt.label_id,
        "source_run_id": str(receipt.source_run_id),
        "applied_at": receipt.applied_at.astimezone(UTC).isoformat(),
    }


def _from_payload(payload: Mapping[str, Any]) -> HistoryEventReceipt:
    schema_version = payload.get("receipt_schema_version")
    if schema_version != _RECEIPT_SCHEMA_VERSION:
        raise ValueError("Unsupported History event receipt schema version")
    applied_raw = payload.get("applied_at")
    if not isinstance(applied_raw, str):
        raise ValueError("History event receipt applied_at is required")
    applied_at = datetime.fromisoformat(applied_raw)
    if applied_at.tzinfo is None or applied_at.utcoffset() is None:
        raise ValueError("History event receipt applied_at must be timezone-aware")
    return HistoryEventReceipt(
        account_id=_required(payload, "account_id"),
        event_id=_required(payload, "event_id"),
        history_id=_required(payload, "history_id"),
        event_type=_required(payload, "event_type"),
        message_id=_required(payload, "message_id"),
        label_id=_optional(payload, "label_id"),
        source_run_id=UUID(_required(payload, "source_run_id")),
        applied_at=applied_at,
        schema_version=_RECEIPT_SCHEMA_VERSION,
    )


def _required(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null")
    return value
