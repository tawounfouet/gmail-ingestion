"""Deterministic parsing for Gmail MessagePart headers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from email.header import decode_header, make_header
from typing import Any


@dataclass(frozen=True, slots=True)
class HeaderEntry:
    """One source header preserving original order and duplicates."""

    index: int
    name: str
    value: str
    decoded_value: str


@dataclass(frozen=True, slots=True)
class ParsedHeaders:
    """All source headers plus a case-insensitive multi-value lookup."""

    entries: tuple[HeaderEntry, ...]
    normalized: dict[str, tuple[str, ...]]

    def values(self, name: str) -> tuple[str, ...]:
        return self.normalized.get(name.lower(), ())

    def first(self, name: str) -> str | None:
        values = self.values(name)
        return values[0] if values else None


class HeaderParseError(ValueError):
    """Raised when Gmail returns a structurally invalid header collection."""


def parse_headers(raw_headers: Any) -> ParsedHeaders:
    """Parse Gmail headers without collapsing duplicate names."""
    if raw_headers is None:
        return ParsedHeaders((), {})
    if not isinstance(raw_headers, Sequence) or isinstance(raw_headers, (str, bytes)):
        raise HeaderParseError("Gmail MessagePart headers must be a list")

    entries: list[HeaderEntry] = []
    grouped: dict[str, list[str]] = {}
    for index, raw in enumerate(raw_headers):
        if not isinstance(raw, Mapping):
            raise HeaderParseError("Gmail MessagePart header must be an object")
        name = raw.get("name")
        value = raw.get("value")
        if not isinstance(name, str) or not name:
            raise HeaderParseError("Gmail MessagePart header is missing name")
        if not isinstance(value, str):
            raise HeaderParseError(f"Gmail header {name!r} is missing string value")
        decoded = decode_header_value(value)
        entries.append(HeaderEntry(index=index, name=name, value=value, decoded_value=decoded))
        grouped.setdefault(name.lower(), []).append(decoded)

    return ParsedHeaders(
        entries=tuple(entries),
        normalized={name: tuple(values) for name, values in grouped.items()},
    )


def decode_header_value(value: str) -> str:
    """Decode RFC 2047 encoded words while preserving undecodable input safely."""
    try:
        return str(make_header(decode_header(value)))
    except (LookupError, UnicodeError):
        return value
