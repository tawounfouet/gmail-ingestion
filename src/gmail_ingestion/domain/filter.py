"""Immutable Gmail discovery filter and date-range contracts."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any


@dataclass(frozen=True, slots=True)
class DateRange:
    """User-requested Gmail date range before relative-date resolution."""

    after: date | None = None
    before: date | None = None
    last_days: int | None = None

    def __post_init__(self) -> None:
        if self.after is None and self.before is None and self.last_days is None:
            raise ValueError("DateRange requires after, before, or last_days")
        if self.last_days is not None:
            if self.last_days <= 0:
                raise ValueError("last_days must be >= 1")
            if self.after is not None or self.before is not None:
                raise ValueError("last_days cannot be combined with after or before")
        if self.after is not None and self.before is not None and self.after > self.before:
            raise ValueError("after must be less than or equal to before")

    def resolve(self, *, at: datetime | None = None) -> ResolvedDateRange:
        """Resolve relative dates exactly once against a timezone-aware run timestamp."""
        if self.last_days is None:
            return ResolvedDateRange(
                requested_after=self.after,
                requested_before=self.before,
                requested_last_days=None,
                resolved_after=self.after,
                resolved_before=self.before,
                resolution_timestamp=None,
            )

        resolved_at = at or datetime.now(UTC)
        if resolved_at.tzinfo is None:
            raise ValueError("relative date resolution timestamp must include a timezone")
        return ResolvedDateRange(
            requested_after=None,
            requested_before=None,
            requested_last_days=self.last_days,
            resolved_after=resolved_at.date() - timedelta(days=self.last_days),
            resolved_before=None,
            resolution_timestamp=resolved_at,
        )


@dataclass(frozen=True, slots=True)
class ResolvedDateRange:
    """Frozen effective Gmail date range for one ingestion run."""

    requested_after: date | None
    requested_before: date | None
    requested_last_days: int | None
    resolved_after: date | None
    resolved_before: date | None
    resolution_timestamp: datetime | None

    @property
    def gmail_query_terms(self) -> tuple[str, ...]:
        """Compile resolved bounds to Gmail-native search terms."""
        terms: list[str] = []
        if self.resolved_after is not None:
            terms.append(f"after:{self.resolved_after.strftime('%Y/%m/%d')}")
        if self.resolved_before is not None:
            terms.append(f"before:{self.resolved_before.strftime('%Y/%m/%d')}")
        return tuple(terms)

    def requested_payload(self) -> dict[str, Any]:
        return {
            "after": self.requested_after.isoformat() if self.requested_after is not None else None,
            "before": (
                self.requested_before.isoformat() if self.requested_before is not None else None
            ),
            "last_days": self.requested_last_days,
        }

    def effective_payload(self) -> dict[str, Any]:
        return {
            "resolved_after": (
                self.resolved_after.isoformat() if self.resolved_after is not None else None
            ),
            "resolved_before": (
                self.resolved_before.isoformat() if self.resolved_before is not None else None
            ),
            "resolution_timestamp": (
                self.resolution_timestamp.isoformat()
                if self.resolution_timestamp is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class GmailFilter:
    """Explicit, serializable scope for Gmail message discovery."""

    query: str | None = None
    label_ids: tuple[str, ...] = ()
    include_spam_trash: bool = False
    date_range: ResolvedDateRange | None = None

    @classmethod
    def from_values(
        cls,
        *,
        query: str | None = None,
        label_ids: Sequence[str] = (),
        include_spam_trash: bool = False,
        after: date | None = None,
        before: date | None = None,
        last_days: int | None = None,
        resolution_timestamp: datetime | None = None,
    ) -> GmailFilter:
        """Build and freeze a filter from public API/CLI values."""
        requested_range: DateRange | None = None
        if after is not None or before is not None or last_days is not None:
            requested_range = DateRange(after=after, before=before, last_days=last_days)

        return cls(
            query=query,
            label_ids=tuple(label_ids),
            include_spam_trash=include_spam_trash,
            date_range=(
                requested_range.resolve(at=resolution_timestamp)
                if requested_range is not None
                else None
            ),
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
    def effective_query(self) -> str | None:
        """Return the deterministic Gmail-native q sent to messages.list."""
        terms: list[str] = []
        if self.query is not None:
            terms.append(self.query)
        if self.date_range is not None:
            terms.extend(self.date_range.gmail_query_terms)
        return " ".join(terms) or None

    @property
    def is_filtered(self) -> bool:
        """Whether discovery uses query, labels, or a bounded date range."""
        return (
            self.query is not None
            or bool(self.canonical_label_ids)
            or self.date_range is not None
        )

    @property
    def scope_kind(self) -> str:
        """Stable control-plane identity for full, filtered, and range discovery."""
        if self.date_range is not None:
            return "range"
        return "filtered" if self.is_filtered else "full"

    def requested_payload(self) -> dict[str, Any]:
        """Serialize the caller-visible filter request."""
        payload: dict[str, Any] = {
            "query": self.query,
            "label_ids": list(self.label_ids),
            "include_spam_trash": self.include_spam_trash,
            "after": None,
            "before": None,
            "last_days": None,
        }
        if self.date_range is not None:
            payload.update(self.date_range.requested_payload())
        return payload

    def effective_payload(self) -> dict[str, Any]:
        """Serialize the canonical values sent to Gmail and frozen for the run."""
        payload: dict[str, Any] = {
            "gmail_q": self.effective_query,
            "label_ids": list(self.canonical_label_ids),
            "include_spam_trash": self.include_spam_trash,
            "resolved_after": None,
            "resolved_before": None,
            "resolution_timestamp": None,
        }
        if self.date_range is not None:
            payload.update(self.date_range.effective_payload())
        return payload

    def _fingerprint_payload(self) -> dict[str, Any]:
        effective = self.effective_payload()
        return {
            "scope_kind": self.scope_kind,
            "gmail_q": effective["gmail_q"],
            "label_ids": effective["label_ids"],
            "include_spam_trash": effective["include_spam_trash"],
            "resolved_after": effective["resolved_after"],
            "resolved_before": effective["resolved_before"],
        }

    @property
    def scope_fingerprint(self) -> str:
        """Return a deterministic SHA-256 identity for the effective discovery scope."""
        canonical = json.dumps(
            self._fingerprint_payload(),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()
