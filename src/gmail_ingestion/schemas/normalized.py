"""Versioned contracts for the normalized Gmail dataset."""

from __future__ import annotations

from dataclasses import dataclass

NORMALIZED_SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class NormalizedField:
    """One stable field in a normalized table contract."""

    name: str
    type: str
    nullable: bool = True


@dataclass(frozen=True, slots=True)
class NormalizedTableContract:
    """Stable grain, logical key and typed field list for one table."""

    name: str
    grain: str
    primary_key: tuple[str, ...]
    fields: tuple[NormalizedField, ...]

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields)


def _f(name: str, type_: str, nullable: bool = True) -> NormalizedField:
    return NormalizedField(name, type_, nullable)


TABLE_CONTRACTS: dict[str, NormalizedTableContract] = {
    "accounts": NormalizedTableContract(
        "accounts",
        "one row per account_id",
        ("account_id",),
        (
            _f("account_id", "string", False),
            _f("email_address", "string"),
            _f("messages_total", "int64"),
            _f("threads_total", "int64"),
            _f("history_id", "string"),
            _f("profile_fetched_at", "timestamp[us, UTC]", False),
            _f("source_connector_version", "string", False),
            _f("dataset_version", "string", False),
        ),
    ),
    "labels": NormalizedTableContract(
        "labels",
        "one row per account_id and label_id",
        ("account_id", "label_id"),
        (
            _f("account_id", "string", False),
            _f("label_id", "string", False),
            _f("name", "string", False),
            _f("label_type", "string"),
            _f("message_list_visibility", "string"),
            _f("label_list_visibility", "string"),
            _f("messages_total", "int64"),
            _f("messages_unread", "int64"),
            _f("threads_total", "int64"),
            _f("threads_unread", "int64"),
            _f("color_text", "string"),
            _f("color_background", "string"),
            _f("is_system", "boolean", False),
            _f("ingested_at", "timestamp[us, UTC]", False),
        ),
    ),
    "messages": NormalizedTableContract(
        "messages",
        "one row per account_id and message_id",
        ("account_id", "message_id"),
        (
            _f("account_id", "string", False),
            _f("message_id", "string", False),
            _f("thread_id", "string", False),
            _f("history_id", "string"),
            _f("internal_date_epoch_ms", "int64"),
            _f("internal_date", "timestamp[us, UTC]"),
            _f("snippet", "string"),
            _f("size_estimate", "int64"),
            _f("top_level_mime_type", "string"),
            _f("subject", "string"),
            _f("from_address", "string"),
            _f("from_name", "string"),
            _f("reply_to_address", "string"),
            _f("rfc822_message_id", "string"),
            _f("in_reply_to", "string"),
            _f("references_raw", "string"),
            _f("text_body", "string"),
            _f("html_body", "string"),
            _f("has_attachments", "boolean", False),
            _f("attachment_count", "int32", False),
            _f("is_deleted", "boolean", False),
            _f("deleted_at", "timestamp[us, UTC]"),
            _f("ingested_at", "timestamp[us, UTC]", False),
            _f("updated_at", "timestamp[us, UTC]", False),
            _f("source_connector_version", "string", False),
            _f("normalized_schema_version", "string", False),
            _f("dataset_version", "string", False),
        ),
    ),
    "threads": NormalizedTableContract(
        "threads",
        "one row per account_id and thread_id",
        ("account_id", "thread_id"),
        (
            _f("account_id", "string", False),
            _f("thread_id", "string", False),
            _f("history_id", "string"),
            _f("snippet", "string"),
            _f("message_count", "int32", False),
            _f("first_message_at", "timestamp[us, UTC]"),
            _f("last_message_at", "timestamp[us, UTC]"),
            _f("first_message_id", "string"),
            _f("last_message_id", "string"),
            _f("participant_count", "int32", False),
            _f("has_attachments", "boolean", False),
            _f("is_deleted", "boolean", False),
            _f("ingested_at", "timestamp[us, UTC]", False),
            _f("updated_at", "timestamp[us, UTC]", False),
        ),
    ),
    "message_headers": NormalizedTableContract(
        "message_headers",
        "one row per header occurrence",
        ("account_id", "message_id", "part_id", "header_index"),
        (
            _f("account_id", "string", False),
            _f("message_id", "string", False),
            _f("part_id", "string", False),
            _f("header_index", "int32", False),
            _f("header_name", "string", False),
            _f("header_name_normalized", "string", False),
            _f("header_value", "string", False),
            _f("is_top_level", "boolean", False),
            _f("ingested_at", "timestamp[us, UTC]", False),
        ),
    ),
    "message_recipients": NormalizedTableContract(
        "message_recipients",
        "one row per recipient and role",
        ("account_id", "message_id", "recipient_role", "recipient_index"),
        (
            _f("account_id", "string", False),
            _f("message_id", "string", False),
            _f("recipient_role", "string", False),
            _f("recipient_index", "int32", False),
            _f("display_name", "string"),
            _f("email_address", "string", False),
            _f("email_domain", "string"),
            _f("is_account_address", "boolean", False),
            _f("ingested_at", "timestamp[us, UTC]", False),
        ),
    ),
    "message_labels": NormalizedTableContract(
        "message_labels",
        "one row per active message-label relation",
        ("account_id", "message_id", "label_id"),
        (
            _f("account_id", "string", False),
            _f("message_id", "string", False),
            _f("label_id", "string", False),
            _f("added_history_id", "string"),
            _f("removed_history_id", "string"),
            _f("is_active", "boolean", False),
            _f("first_seen_at", "timestamp[us, UTC]", False),
            _f("last_seen_at", "timestamp[us, UTC]", False),
        ),
    ),
    "mime_parts": NormalizedTableContract(
        "mime_parts",
        "one row per MIME part",
        ("account_id", "message_id", "part_path"),
        (
            _f("account_id", "string", False),
            _f("message_id", "string", False),
            _f("part_id", "string", False),
            _f("part_path", "string", False),
            _f("parent_part_path", "string"),
            _f("depth", "int32", False),
            _f("mime_type", "string", False),
            _f("filename", "string"),
            _f("body_size", "int64", False),
            _f("attachment_id", "string"),
            _f("has_inline_data", "boolean", False),
            _f("is_container", "boolean", False),
            _f("is_text", "boolean", False),
            _f("is_html", "boolean", False),
            _f("is_attachment_candidate", "boolean", False),
            _f("content_disposition", "string"),
            _f("content_id", "string"),
            _f("ingested_at", "timestamp[us, UTC]", False),
        ),
    ),
    "attachments": NormalizedTableContract(
        "attachments",
        "one row per attachment logical reference",
        ("account_id", "message_id", "attachment_id"),
        (
            _f("account_id", "string", False),
            _f("message_id", "string", False),
            _f("attachment_id", "string"),
            _f("part_id", "string", False),
            _f("part_path", "string", False),
            _f("filename", "string"),
            _f("mime_type", "string", False),
            _f("size_bytes", "int64", False),
            _f("content_hash_sha256", "string"),
            _f("storage_uri", "string"),
            _f("download_status", "string", False),
            _f("downloaded_at", "timestamp[us, UTC]"),
            _f("is_inline", "boolean", False),
            _f("content_id", "string"),
            _f("ingested_at", "timestamp[us, UTC]", False),
        ),
    ),
}

TABLE_SORT_KEYS: dict[str, tuple[str, ...]] = {
    name: contract.primary_key for name, contract in TABLE_CONTRACTS.items()
}
TABLE_SORT_KEYS["attachments"] = ("account_id", "message_id", "part_path")
