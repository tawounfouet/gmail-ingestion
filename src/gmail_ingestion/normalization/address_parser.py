"""Mailbox address parsing for Gmail header values."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from email.utils import getaddresses


@dataclass(frozen=True, slots=True)
class ParsedAddress:
    """Display name and normalized mailbox address."""

    display_name: str
    address: str


def parse_addresses(values: Sequence[str]) -> tuple[ParsedAddress, ...]:
    """Parse one or more RFC-style address header values."""
    parsed: list[ParsedAddress] = []
    for display_name, address in getaddresses(list(values)):
        normalized = normalize_address(address)
        if not normalized:
            continue
        parsed.append(ParsedAddress(display_name=display_name.strip(), address=normalized))
    return tuple(parsed)


def normalize_address(address: str) -> str:
    """Trim an address and lowercase only its DNS domain component."""
    cleaned = address.strip()
    if "@" not in cleaned:
        return cleaned
    local_part, domain = cleaned.rsplit("@", 1)
    return f"{local_part}@{domain.lower()}"
