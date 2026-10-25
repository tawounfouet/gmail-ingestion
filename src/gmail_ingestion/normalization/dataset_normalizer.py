"""Deterministic RAW Gmail to normalized dataset transformation."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pyingestkit.dataset import Dataset

from gmail_ingestion.domain import (
    AttachmentDownloadStatus,
    AttachmentRecord,
    GmailAccount,
)
from gmail_ingestion.schemas import NORMALIZED_SCHEMA_VERSION, TABLE_CONTRACTS

from .address_parser import ParsedAddress
from .header_parser import parse_headers
from .label_normalizer import normalize_labels
from .mime_walker import MimeAnalysis, analyze_message


class GmailNormalizationError(ValueError):
    """Raised when RAW Gmail data cannot satisfy the normalized contract."""


@dataclass(frozen=True, slots=True)
class NormalizedDataset:
    """All v1 business tables produced by one deterministic normalization pass."""

    tables: dict[str, Dataset]

    def rows(self, name: str) -> list[dict[str, Any]]:
        try:
            return self.tables[name].to_rows()
        except KeyError as exc:
            raise KeyError(f"Unknown normalized table {name!r}") from exc


def normalize_snapshot(
    account: GmailAccount,
    label_payloads: Sequence[Mapping[str, Any]],
    message_payloads: Sequence[Mapping[str, Any]],
    *,
    ingested_at: datetime,
    dataset_version: str,
    source_connector_version: str,
    attachment_records: Mapping[str, Sequence[AttachmentRecord]] | None = None,
) -> NormalizedDataset:
    """Normalize one logical snapshot without source API calls or hidden state."""
    timestamp = _utc(ingested_at)
    if not dataset_version:
        raise GmailNormalizationError("dataset_version must not be empty")
    if not source_connector_version:
        raise GmailNormalizationError("source_connector_version must not be empty")

    rows: dict[str, list[dict[str, Any]]] = {
        name: [] for name in TABLE_CONTRACTS
    }
    rows["accounts"].append(
        {
            "account_id": account.account_id,
            "email_address": account.email_address,
            "messages_total": account.messages_total,
            "threads_total": account.threads_total,
            "history_id": account.history_id,
            "profile_fetched_at": timestamp,
            "source_connector_version": source_connector_version,
            "dataset_version": dataset_version,
        }
    )
    rows["labels"].extend(
        normalize_labels(
            account.account_id,
            label_payloads,
            ingested_at=timestamp,
        ).to_rows()
    )

    attachment_map = attachment_records or {}
    for payload in message_payloads:
        message_id = _required_str(payload.get("id"), "message id")
        analysis = analyze_message(payload)
        records = tuple(attachment_map.get(message_id, ()))
        if not records:
            records = _metadata_only_attachment_records(analysis)
        bundle = _normalize_message(
            account,
            payload,
            analysis,
            records,
            timestamp,
            dataset_version,
            source_connector_version,
        )
        for name, values in bundle.items():
            rows[name].extend(values)

    rows["threads"] = _aggregate_threads(
        account.account_id,
        rows["messages"],
        rows["message_recipients"],
        timestamp,
    )

    tables = {
        name: Dataset(values, fields=TABLE_CONTRACTS[name].field_names)
        for name, values in rows.items()
    }
    return NormalizedDataset(tables)


def _normalize_message(
    account: GmailAccount,
    payload: Mapping[str, Any],
    analysis: MimeAnalysis,
    attachments: Sequence[AttachmentRecord],
    ingested_at: datetime,
    dataset_version: str,
    source_connector_version: str,
) -> dict[str, list[dict[str, Any]]]:
    message_id = _required_str(payload.get("id"), "message id")
    thread_id = _required_str(payload.get("threadId"), "thread id")
    history_id = _optional_str(payload.get("historyId"))
    internal_epoch = _optional_int(payload.get("internalDate"), "internalDate")
    internal_date = (
        datetime.fromtimestamp(internal_epoch / 1000, tz=UTC)
        if internal_epoch is not None
        else None
    )
    root = payload.get("payload")
    root_mapping = root if isinstance(root, Mapping) else {}
    from_address = _first_address(analysis.addresses.get("from", ()))
    reply_to = _first_address(analysis.addresses.get("reply_to", ()))

    messages = [
        {
            "account_id": account.account_id,
            "message_id": message_id,
            "thread_id": thread_id,
            "history_id": history_id,
            "internal_date_epoch_ms": internal_epoch,
            "internal_date": internal_date,
            "snippet": _optional_str(payload.get("snippet")),
            "size_estimate": _optional_int(payload.get("sizeEstimate"), "sizeEstimate"),
            "top_level_mime_type": _optional_str(root_mapping.get("mimeType")),
            "subject": analysis.headers.first("subject"),
            "from_address": from_address.address if from_address is not None else None,
            "from_name": from_address.display_name if from_address is not None else None,
            "reply_to_address": reply_to.address if reply_to is not None else None,
            "rfc822_message_id": analysis.headers.first("message-id"),
            "in_reply_to": analysis.headers.first("in-reply-to"),
            "references_raw": analysis.headers.first("references"),
            "text_body": analysis.text_body,
            "html_body": analysis.html_body,
            "has_attachments": bool(attachments),
            "attachment_count": len(attachments),
            "is_deleted": False,
            "deleted_at": None,
            "ingested_at": ingested_at,
            "updated_at": ingested_at,
            "source_connector_version": source_connector_version,
            "normalized_schema_version": NORMALIZED_SCHEMA_VERSION,
            "dataset_version": dataset_version,
        }
    ]

    return {
        "messages": messages,
        "message_headers": _header_rows(
            account.account_id, message_id, root_mapping, ingested_at
        ),
        "message_recipients": _recipient_rows(
            account, message_id, analysis, ingested_at
        ),
        "message_labels": _message_label_rows(
            account.account_id, message_id, payload, ingested_at
        ),
        "mime_parts": [
            {
                "account_id": account.account_id,
                "message_id": message_id,
                "part_id": part.part_id,
                "part_path": part.part_path,
                "parent_part_path": part.parent_part_path,
                "depth": part.depth,
                "mime_type": part.mime_type,
                "filename": part.filename or None,
                "body_size": part.body_size,
                "attachment_id": part.attachment_id,
                "has_inline_data": part.has_inline_data,
                "is_container": part.is_container,
                "is_text": part.is_text,
                "is_html": part.is_html,
                "is_attachment_candidate": part.is_attachment_candidate,
                "content_disposition": part.content_disposition,
                "content_id": part.content_id,
                "ingested_at": ingested_at,
            }
            for part in analysis.parts
        ],
        "attachments": [
            {
                "account_id": account.account_id,
                "message_id": message_id,
                "attachment_id": record.attachment_id,
                "part_id": record.part_id,
                "part_path": record.part_path,
                "filename": record.filename or None,
                "mime_type": record.mime_type,
                "size_bytes": record.size_bytes,
                "content_hash_sha256": record.content_hash_sha256,
                "storage_uri": record.storage_uri,
                "download_status": record.download_status.value,
                "downloaded_at": record.downloaded_at,
                "is_inline": record.is_inline,
                "content_id": record.content_id,
                "ingested_at": ingested_at,
            }
            for record in attachments
        ],
    }


def _header_rows(
    account_id: str,
    message_id: str,
    root: Mapping[str, Any],
    ingested_at: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def visit(part: Mapping[str, Any], path: str, *, top_level: bool) -> None:
        parsed = parse_headers(part.get("headers"))
        part_id = part.get("partId")
        normalized_part_id = part_id if isinstance(part_id, str) else ""
        for entry in parsed.entries:
            rows.append(
                {
                    "account_id": account_id,
                    "message_id": message_id,
                    "part_id": normalized_part_id or path,
                    "header_index": entry.index,
                    "header_name": entry.name,
                    "header_name_normalized": entry.name.lower(),
                    "header_value": entry.decoded_value,
                    "is_top_level": top_level,
                    "ingested_at": ingested_at,
                }
            )
        children = part.get("parts", [])
        if isinstance(children, Sequence) and not isinstance(children, (str, bytes)):
            for index, child in enumerate(children):
                if isinstance(child, Mapping):
                    visit(child, f"{path}/{index}", top_level=False)

    visit(root, "root", top_level=True)
    return rows


def _recipient_rows(
    account: GmailAccount,
    message_id: str,
    analysis: MimeAnalysis,
    ingested_at: datetime,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    role_map = {
        "from": "FROM",
        "to": "TO",
        "cc": "CC",
        "bcc": "BCC",
        "reply_to": "REPLY_TO",
    }
    account_address = account.email_address.strip().lower()
    for source_role, normalized_role in role_map.items():
        for index, address in enumerate(analysis.addresses.get(source_role, ())):
            email = address.address
            domain = email.rsplit("@", 1)[1] if "@" in email else None
            rows.append(
                {
                    "account_id": account.account_id,
                    "message_id": message_id,
                    "recipient_role": normalized_role,
                    "recipient_index": index,
                    "display_name": address.display_name or None,
                    "email_address": email,
                    "email_domain": domain,
                    "is_account_address": email.lower() == account_address,
                    "ingested_at": ingested_at,
                }
            )
    return rows


def _message_label_rows(
    account_id: str,
    message_id: str,
    payload: Mapping[str, Any],
    ingested_at: datetime,
) -> list[dict[str, Any]]:
    raw_labels = payload.get("labelIds", [])
    if raw_labels is None:
        return []
    if not isinstance(raw_labels, Sequence) or isinstance(raw_labels, (str, bytes)):
        raise GmailNormalizationError("Gmail message labelIds must be a list")
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_labels:
        label_id = _required_str(item, "label id")
        if label_id in seen:
            continue
        seen.add(label_id)
        result.append(
            {
                "account_id": account_id,
                "message_id": message_id,
                "label_id": label_id,
                "added_history_id": None,
                "removed_history_id": None,
                "is_active": True,
                "first_seen_at": ingested_at,
                "last_seen_at": ingested_at,
            }
        )
    return result


def _aggregate_threads(
    account_id: str,
    messages: Sequence[Mapping[str, Any]],
    recipients: Sequence[Mapping[str, Any]],
    ingested_at: datetime,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for message in messages:
        grouped[str(message["thread_id"])].append(message)

    participants: dict[str, set[str]] = defaultdict(set)
    message_to_thread = {
        str(message["message_id"]): str(message["thread_id"]) for message in messages
    }
    for recipient in recipients:
        message_id = str(recipient["message_id"])
        thread_id = message_to_thread.get(message_id)
        email = recipient.get("email_address")
        if thread_id is not None and isinstance(email, str) and email:
            participants[thread_id].add(email.lower())

    result: list[dict[str, Any]] = []
    for thread_id, thread_messages in grouped.items():
        ordered = sorted(
            thread_messages,
            key=lambda row: (
                row.get("internal_date") or datetime.min.replace(tzinfo=UTC),
                str(row["message_id"]),
            ),
        )
        first = ordered[0]
        last = ordered[-1]
        result.append(
            {
                "account_id": account_id,
                "thread_id": thread_id,
                "history_id": _max_history_id(thread_messages),
                "snippet": last.get("snippet"),
                "message_count": len(thread_messages),
                "first_message_at": first.get("internal_date"),
                "last_message_at": last.get("internal_date"),
                "first_message_id": str(first["message_id"]),
                "last_message_id": str(last["message_id"]),
                "participant_count": len(participants.get(thread_id, set())),
                "has_attachments": any(bool(row.get("has_attachments")) for row in thread_messages),
                "is_deleted": all(bool(row.get("is_deleted")) for row in thread_messages),
                "ingested_at": ingested_at,
                "updated_at": ingested_at,
            }
        )
    return result


def _metadata_only_attachment_records(
    analysis: MimeAnalysis,
) -> tuple[AttachmentRecord, ...]:
    return tuple(
        AttachmentRecord(
            attachment_id=item.attachment_id,
            part_id=item.part_id,
            part_path=item.part_path,
            filename=item.filename,
            mime_type=item.mime_type,
            size_bytes=item.size_bytes,
            content_hash_sha256=None,
            storage_uri=None,
            download_status=AttachmentDownloadStatus.METADATA_ONLY,
            downloaded_at=None,
            is_inline=item.is_inline,
            content_id=item.content_id,
        )
        for item in analysis.attachments
    )


def _first_address(values: Sequence[ParsedAddress]) -> ParsedAddress | None:
    return values[0] if values else None


def _max_history_id(messages: Sequence[Mapping[str, Any]]) -> str | None:
    values = [str(row["history_id"]) for row in messages if row.get("history_id") is not None]
    if not values:
        return None

    def key(value: str) -> tuple[int, int | str]:
        try:
            return (1, int(value))
        except ValueError:
            return (0, value)

    return max(values, key=key)


def _required_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise GmailNormalizationError(f"Missing or invalid {name}")
    return value


def _optional_str(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def _optional_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise GmailNormalizationError(f"{name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise GmailNormalizationError(f"{name} must be an integer") from exc


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise GmailNormalizationError("ingested_at must be timezone-aware")
    return value.astimezone(UTC)
