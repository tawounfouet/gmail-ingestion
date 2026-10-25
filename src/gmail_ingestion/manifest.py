"""Versioned run manifest for Gmail ingestion."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from pyingestkit.artifacts.base import ArtifactStore
from pyingestkit.core.exceptions import StorageError

from gmail_ingestion._version import __version__
from gmail_ingestion.domain import GmailFilter

_JOB_ID = "gmail.messages"
_MANIFEST_PATH = "manifest.json"


@dataclass(frozen=True, slots=True)
class ManifestIssue:
    """Structured error or warning attached to one run."""

    scope: str
    code: str
    message: str
    message_id: str | None = None
    attachment_id: str | None = None

    def to_payload(self) -> dict[str, str | None]:
        return {
            "scope": self.scope,
            "code": self.code,
            "message": self.message,
            "message_id": self.message_id,
            "attachment_id": self.attachment_id,
        }


@dataclass(frozen=True, slots=True)
class GmailRunManifest:
    """Serializable manifest contract, versioned independently from business schemas."""

    run_id: UUID
    account_id: str
    started_at: datetime
    completed_at: datetime | None
    status: str
    history_start: str | None
    include_spam_trash: bool
    download_attachments: bool
    max_attachment_bytes: int | None
    messages_discovered: int
    messages_fetched: int
    messages_failed: int
    message_limit: int | None = None
    query: str | None = None
    label_ids: tuple[str, ...] = ()
    mime_parts: int = 0
    mime_messages_processed: int = 0
    mime_messages_failed: int = 0
    attachments_detected: int = 0
    attachments_fetched: int = 0
    attachments_failed: int = 0
    attachments_skipped: int = 0
    bytes_downloaded: int = 0
    resumed: bool = False
    errors: tuple[ManifestIssue, ...] = ()
    warnings: tuple[ManifestIssue, ...] = ()
    storage_paths: dict[str, str] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        gmail_filter = GmailFilter(
            query=self.query,
            label_ids=self.label_ids,
            include_spam_trash=self.include_spam_trash,
        )
        if self.message_limit is not None:
            mode = "sample_snapshot"
        elif gmail_filter.is_filtered:
            mode = "filtered_snapshot"
        else:
            mode = "full_snapshot"

        return {
            "schema_version": 1,
            "connector_name": "gmail-ingestion",
            "connector_version": __version__,
            "run_id": str(self.run_id),
            "account_id": self.account_id,
            "started_at": self.started_at.isoformat(),
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at is not None else None
            ),
            "status": self.status,
            "mode": mode,
            "scope": {
                "kind": gmail_filter.scope_kind,
                "fingerprint": gmail_filter.scope_fingerprint,
            },
            "filters": {
                "query": self.query,
                "label_ids": list(gmail_filter.canonical_label_ids),
            },
            "requested_filter": gmail_filter.requested_payload(),
            "effective_filter": gmail_filter.effective_payload(),
            "message_limit": self.message_limit,
            "message_format": "full",
            "include_spam_trash": self.include_spam_trash,
            "download_attachments": self.download_attachments,
            "max_attachment_bytes": self.max_attachment_bytes,
            "input_checkpoint": None,
            "candidate_checkpoint": self.history_start,
            "committed_checkpoint": None,
            "history_start": self.history_start,
            "messages_discovered": self.messages_discovered,
            "messages_fetched": self.messages_fetched,
            "messages_failed": self.messages_failed,
            "mime_parts": self.mime_parts,
            "mime_messages_processed": self.mime_messages_processed,
            "mime_messages_failed": self.mime_messages_failed,
            "attachments_detected": self.attachments_detected,
            "attachments_fetched": self.attachments_fetched,
            "attachments_failed": self.attachments_failed,
            "attachments_skipped": self.attachments_skipped,
            "bytes_downloaded": self.bytes_downloaded,
            "dataset_version": None,
            "resumed": self.resumed,
            "errors": [issue.to_payload() for issue in self.errors],
            "warnings": [issue.to_payload() for issue in self.warnings],
            "storage_paths": self.storage_paths,
        }


class ManifestWriter:
    """Persist and resume a run-level Gmail manifest."""

    def __init__(self, store: ArtifactStore, *, job_id: str = _JOB_ID) -> None:
        self.store = store
        self.job_id = job_id

    def load_payload(self, run_id: UUID) -> dict[str, Any] | None:
        try:
            raw = self.store.read_bytes(
                self.store.uri_for(self.job_id, run_id, _MANIFEST_PATH)
            )
        except StorageError:
            return None
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return cast(dict[str, Any], payload) if isinstance(payload, dict) else None

    def existing_started_at(self, run_id: UUID) -> datetime | None:
        payload = self.load_payload(run_id)
        if payload is None:
            return None
        value = payload.get("started_at")
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    def validate_resume_settings(
        self,
        run_id: UUID,
        *,
        include_spam_trash: bool,
        download_attachments: bool,
        max_attachment_bytes: int | None,
        message_limit: int | None = None,
        query: str | None = None,
        label_ids: tuple[str, ...] = (),
    ) -> None:
        payload = self.load_payload(run_id)
        if payload is None:
            return

        gmail_filter = GmailFilter(
            query=query,
            label_ids=label_ids,
            include_spam_trash=include_spam_trash,
        )
        filters = payload.get("filters", {})
        actual_label_ids: tuple[str, ...] = ()
        if isinstance(filters, dict):
            raw_label_ids = filters.get("label_ids", [])
            if isinstance(raw_label_ids, list) and all(
                isinstance(label_id, str) for label_id in raw_label_ids
            ):
                actual_label_ids = tuple(sorted(set(raw_label_ids)))

        expected = {
            "include_spam_trash": include_spam_trash,
            "download_attachments": download_attachments,
            "max_attachment_bytes": max_attachment_bytes,
            "message_limit": message_limit,
            "query": query,
            "label_ids": gmail_filter.canonical_label_ids,
        }
        for key, value in expected.items():
            if key == "query":
                actual = filters.get("query") if isinstance(filters, dict) else None
            elif key == "label_ids":
                actual = actual_label_ids
            else:
                actual = payload.get(key)
            if actual != value:
                raise ValueError(
                    f"Cannot resume run {run_id}: setting {key!r} differs from the manifest"
                )

        scope = payload.get("scope")
        if isinstance(scope, dict):
            actual_fingerprint = scope.get("fingerprint")
            if (
                isinstance(actual_fingerprint, str)
                and actual_fingerprint != gmail_filter.scope_fingerprint
            ):
                raise ValueError(
                    f"Cannot resume run {run_id}: effective Gmail filter scope differs "
                    "from the manifest"
                )

    def write(self, manifest: GmailRunManifest) -> str:
        path = self.store.write_json(
            self.job_id,
            manifest.run_id,
            _MANIFEST_PATH,
            manifest.to_payload(),
        )
        return str(path)
