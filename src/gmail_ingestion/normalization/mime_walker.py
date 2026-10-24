"""Recursive Gmail MIME traversal and body extraction."""

from __future__ import annotations

import base64
import binascii
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.message import Message
from typing import Any

from gmail_ingestion.domain import AttachmentCandidate

from .address_parser import ParsedAddress, parse_addresses
from .header_parser import ParsedHeaders, parse_headers


class MimeParseError(ValueError):
    """Raised when a Gmail MIME payload cannot be interpreted safely."""


@dataclass(frozen=True, slots=True)
class MimePartRecord:
    """Technical representation of one node in the Gmail MIME tree."""

    part_id: str
    part_path: str
    parent_part_path: str | None
    depth: int
    mime_type: str
    filename: str
    body_size: int
    attachment_id: str | None
    has_inline_data: bool
    is_container: bool
    is_text: bool
    is_html: bool
    is_attachment_candidate: bool
    content_disposition: str | None
    content_id: str | None


@dataclass(frozen=True, slots=True)
class MimeAnalysis:
    """Deterministic technical analysis derived from one raw Gmail message."""

    headers: ParsedHeaders
    addresses: dict[str, tuple[ParsedAddress, ...]]
    parts: tuple[MimePartRecord, ...]
    attachments: tuple[AttachmentCandidate, ...]
    text_body: str | None
    html_body: str | None
    warnings: tuple[str, ...]


def analyze_message(message: Mapping[str, Any]) -> MimeAnalysis:
    """Walk a full Gmail Message payload recursively without network calls."""
    payload = message.get("payload")
    if payload is None:
        return MimeAnalysis(
            headers=ParsedHeaders((), {}),
            addresses={},
            parts=(),
            attachments=(),
            text_body=None,
            html_body=None,
            warnings=("message has no MIME payload",),
        )
    if not isinstance(payload, Mapping):
        raise MimeParseError("Gmail message payload must be an object")

    root_headers = parse_headers(payload.get("headers"))
    addresses = {
        role: parse_addresses(root_headers.values(header_name))
        for role, header_name in (
            ("from", "from"),
            ("to", "to"),
            ("cc", "cc"),
            ("bcc", "bcc"),
            ("reply_to", "reply-to"),
        )
    }

    parts: list[MimePartRecord] = []
    attachments: list[AttachmentCandidate] = []
    text_bodies: list[str] = []
    html_bodies: list[str] = []
    warnings: list[str] = []

    _walk_part(
        payload,
        path="root",
        parent_path=None,
        depth=0,
        parts=parts,
        attachments=attachments,
        text_bodies=text_bodies,
        html_bodies=html_bodies,
        warnings=warnings,
    )

    return MimeAnalysis(
        headers=root_headers,
        addresses=addresses,
        parts=tuple(parts),
        attachments=tuple(attachments),
        text_body=_join_bodies(text_bodies),
        html_body=_join_bodies(html_bodies),
        warnings=tuple(warnings),
    )


def decode_base64url(data: str) -> bytes:
    """Decode Gmail base64url data with optional omitted padding."""
    try:
        encoded = data.encode("ascii")
    except UnicodeEncodeError as exc:
        raise MimeParseError("Gmail body data is not ASCII base64url") from exc
    padded = encoded + b"=" * (-len(encoded) % 4)
    try:
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MimeParseError("Gmail body contains invalid base64url data") from exc


def _walk_part(
    raw_part: Mapping[str, Any],
    *,
    path: str,
    parent_path: str | None,
    depth: int,
    parts: list[MimePartRecord],
    attachments: list[AttachmentCandidate],
    text_bodies: list[str],
    html_bodies: list[str],
    warnings: list[str],
) -> None:
    headers = parse_headers(raw_part.get("headers"))
    part_id = _string(raw_part.get("partId"))
    mime_type = _string(raw_part.get("mimeType")).lower() or "application/octet-stream"
    filename = _string(raw_part.get("filename"))

    raw_body = raw_part.get("body")
    if raw_body is None:
        body: Mapping[str, Any] = {}
    elif isinstance(raw_body, Mapping):
        body = raw_body
    else:
        raise MimeParseError(f"MIME part {path} body must be an object")

    attachment_id = _optional_string(body.get("attachmentId"))
    inline_data = _optional_string(body.get("data"))
    body_size = _non_negative_int(body.get("size", 0), f"MIME part {path} size")
    disposition = _content_disposition(headers.first("content-disposition"))
    content_id = _content_id(headers.first("content-id"))

    raw_children = raw_part.get("parts", [])
    if raw_children is None:
        raw_children = []
    if not isinstance(raw_children, Sequence) or isinstance(raw_children, (str, bytes)):
        raise MimeParseError(f"MIME part {path} children must be a list")

    is_container = bool(raw_children) or mime_type.startswith("multipart/")
    is_text = mime_type == "text/plain"
    is_html = mime_type == "text/html"
    is_attachment = bool(
        attachment_id
        or filename
        or disposition == "attachment"
        or (disposition == "inline" and content_id)
    )

    parts.append(
        MimePartRecord(
            part_id=part_id,
            part_path=path,
            parent_part_path=parent_path,
            depth=depth,
            mime_type=mime_type,
            filename=filename,
            body_size=body_size,
            attachment_id=attachment_id,
            has_inline_data=inline_data is not None,
            is_container=is_container,
            is_text=is_text,
            is_html=is_html,
            is_attachment_candidate=is_attachment,
            content_disposition=disposition,
            content_id=content_id,
        )
    )

    if is_attachment:
        attachments.append(
            AttachmentCandidate(
                attachment_id=attachment_id,
                part_id=part_id,
                part_path=path,
                filename=filename,
                mime_type=mime_type,
                size_bytes=body_size,
                is_inline=disposition == "inline",
                content_id=content_id,
                inline_data=inline_data,
            )
        )

    if inline_data is not None and not is_attachment:
        if is_text:
            text_bodies.append(
                _decode_text(inline_data, headers.first("content-type"), path, warnings)
            )
        elif is_html:
            html_bodies.append(
                _decode_text(inline_data, headers.first("content-type"), path, warnings)
            )

    for index, child in enumerate(raw_children):
        if not isinstance(child, Mapping):
            raise MimeParseError(f"MIME part {path}/{index} must be an object")
        _walk_part(
            child,
            path=f"{path}/{index}",
            parent_path=path,
            depth=depth + 1,
            parts=parts,
            attachments=attachments,
            text_bodies=text_bodies,
            html_bodies=html_bodies,
            warnings=warnings,
        )


def _decode_text(
    data: str,
    content_type: str | None,
    path: str,
    warnings: list[str],
) -> str:
    raw = decode_base64url(data)
    charset = _charset(content_type) or "utf-8"
    try:
        return raw.decode(charset, errors="strict")
    except LookupError:
        warnings.append(f"{path}: unknown charset {charset!r}; decoded as UTF-8 with replacement")
        return raw.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        warnings.append(f"{path}: invalid {charset} bytes; replacement characters inserted")
        return raw.decode(charset, errors="replace")


def _charset(content_type: str | None) -> str | None:
    if not content_type:
        return None
    message = Message()
    message["content-type"] = content_type
    value = message.get_content_charset()
    return value.lower() if isinstance(value, str) else None


def _content_disposition(value: str | None) -> str | None:
    if not value:
        return None
    disposition = value.split(";", 1)[0].strip().lower()
    return disposition or None


def _content_id(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().strip("<>").strip()
    return normalized or None


def _join_bodies(values: list[str]) -> str | None:
    if not values:
        return None
    return "\n\n".join(values)


def _string(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _non_negative_int(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise MimeParseError(f"{name} must be a non-negative integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise MimeParseError(f"{name} must be a non-negative integer") from exc
    if result < 0:
        raise MimeParseError(f"{name} must be a non-negative integer")
    return result
