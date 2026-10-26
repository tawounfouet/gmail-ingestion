"""Reliability contracts for projection delta continuity and convergence."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from gmail_ingestion.domain import GmailHistoryEvent, HistoryProcessingStatus


class ProjectionGapError(ValueError):
    """Raised when persisted delta batches do not form a continuous projection frontier."""


class ProjectionConvergenceError(RuntimeError):
    """Raised when incremental projection and full rebuild do not converge logically."""


@dataclass(frozen=True, slots=True)
class ProjectionDeltaBatch:
    """One committed incremental run represented as an explicit History frontier transition."""

    account_id: str
    run_id: UUID
    input_history_id: str
    output_history_id: str
    events: tuple[GmailHistoryEvent, ...]

    def __post_init__(self) -> None:
        if not self.account_id.strip():
            raise ValueError("account_id must not be empty")
        start = _history_int(self.input_history_id)
        end = _history_int(self.output_history_id)
        if end < start:
            raise ValueError("output_history_id must not precede input_history_id")
        seen: set[str] = set()
        for event in self.events:
            if event.account_id != self.account_id:
                raise ValueError("delta batch contains an event for another account")
            if event.run_id != self.run_id:
                raise ValueError("delta batch contains an event from another run")
            if event.processing_status is not HistoryProcessingStatus.PROCESSED:
                raise ValueError("delta batch requires PROCESSED history events")
            history = _history_int(event.history_id)
            if not start < history <= end:
                raise ValueError(
                    f"event history id {event.history_id} is outside batch frontier "
                    f"({self.input_history_id}, {self.output_history_id}]"
                )
            if event.event_id in seen:
                raise ValueError("delta batch must not contain duplicate event IDs")
            seen.add(event.event_id)


@dataclass(frozen=True, slots=True)
class ProjectionConvergenceResult:
    """Evidence that incremental projection equals deterministic full rebuild."""

    projection_id: str
    target_history_id: str
    incremental_digest: str
    rebuild_digest: str
    equal: bool
    incremental_batch_count: int
    rebuild_batch_count: int


def validate_delta_chain(
    *,
    account_id: str,
    start_history_id: str,
    target_history_id: str,
    batches: tuple[ProjectionDeltaBatch, ...],
) -> tuple[GmailHistoryEvent, ...]:
    """Validate explicit checkpoint continuity without assuming Gmail History IDs are contiguous."""
    start = _history_int(start_history_id)
    target = _history_int(target_history_id)
    if target < start:
        raise ProjectionGapError("target History frontier precedes start frontier")

    if not batches:
        if start != target:
            raise ProjectionGapError(
                f"No delta batch covers frontier {start_history_id!r} -> {target_history_id!r}"
            )
        return ()

    expected = start_history_id
    events: list[GmailHistoryEvent] = []
    seen_event_ids: set[str] = set()

    for index, batch in enumerate(batches):
        if batch.account_id != account_id:
            raise ProjectionGapError(f"delta batch {index} belongs to another account")
        if batch.input_history_id != expected:
            raise ProjectionGapError(
                "Projection delta gap detected: "
                f"expected batch input {expected!r}, got {batch.input_history_id!r}"
            )
        if _history_int(batch.output_history_id) < _history_int(batch.input_history_id):
            raise ProjectionGapError("delta batch output precedes its input")
        expected = batch.output_history_id
        for event in batch.events:
            if event.event_id in seen_event_ids:
                continue
            seen_event_ids.add(event.event_id)
            events.append(event)

    if expected != target_history_id:
        raise ProjectionGapError(
            "Projection delta chain does not reach requested target: "
            f"ended at {expected!r}, expected {target_history_id!r}"
        )

    return tuple(events)


def _history_int(value: str) -> int:
    if not value or not value.strip():
        raise ValueError("history id must not be empty")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"history id must be numeric: {value!r}") from exc
