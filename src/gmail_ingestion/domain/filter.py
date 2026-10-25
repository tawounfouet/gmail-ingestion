"""Immutable Gmail discovery filter contract."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GmailFilter:
    """Explicit, serializable scope for Gmail message discovery."""

    query: str | None = None
    label_ids: tuple[str, ...] = ()
    include_spam_trash: bool = False

    @classmethod
    def from_values(
        cls,
        *,
        query: str | None = None,
        label_ids: Sequence[str] = (),
        include_spam_trash: bool = False,
    ) -> GmailFilter:
        """Build a filter from public API/CLI values without mutating caller-owned inputs."""
        return cls(
            query=query,
            label_ids=tuple(label_ids),
            include_spam_trash=include_spam_trash,
        )

    def __post_init__(self) -> None:
        if self.query is not None and not self.query.strip():
            raise ValueError("query must not be empty when provided")
        for label_id in self.label_ids:
            if not label_id or not label_id.strip():
                raise ValueError("label_ids must not contain empty values")
            if label_id != label_id.strip():
                raise ValueError("label_ids must not contain leading or trailing whitespace")

    @property
    def canonical_label_ids(self) -> tuple[str, ...]:
        """Return label IDs in stable order with duplicate values removed."""
        return tuple(sorted(set(self.label_ids)))

    @property
    def is_filtered(self) -> bool:
        """Whether the discovery scope is narrower than the ordinary mailbox snapshot."""
        return self.query is not None or bool(self.canonical_label_ids)

    @property
    def scope_kind(self) -> str:
        """Stable control-plane identity for full versus filtered discovery."""
        return "filtered" if self.is_filtered else "full"

    def requested_payload(self) -> dict[str, Any]:
        """Serialize the caller-visible filter request."""
        return {
            "query": self.query,
            "label_ids": list(self.label_ids),
            "include_spam_trash": self.include_spam_trash,
        }

    def effective_payload(self) -> dict[str, Any]:
        """Serialize the canonical values sent to Gmail."""
        return {
            "gmail_q": self.query,
            "label_ids": list(self.canonical_label_ids),
            "include_spam_trash": self.include_spam_trash,
        }

    @property
    def scope_fingerprint(self) -> str:
        """Return a deterministic SHA-256 identity for the effective discovery scope."""
        canonical = json.dumps(
            self.effective_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()
