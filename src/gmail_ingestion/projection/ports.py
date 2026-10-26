"""Ports owned by the projection application/domain boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .models import CurrentPointer, ProjectionCandidate, ProjectionStatus


class ProjectionStore(Protocol):
    """Persistence contract for projection candidates, versions and current pointer."""

    def save_candidate(self, candidate: ProjectionCandidate) -> None: ...

    def load_candidate(self, account_id: str, projection_id: str) -> ProjectionCandidate | None: ...

    def require_candidate(self, account_id: str, projection_id: str) -> ProjectionCandidate: ...

    def update_candidate(
        self,
        candidate: ProjectionCandidate,
        *,
        expected_status: ProjectionStatus,
    ) -> None: ...

    def discard_candidate(self, account_id: str, projection_id: str) -> None: ...

    def load_current(self, account_id: str) -> CurrentPointer | None: ...

    def load_version(self, account_id: str, projection_id: str) -> ProjectionCandidate | None: ...

    def promote(
        self,
        account_id: str,
        projection_id: str,
        *,
        promoted_at: datetime | None = None,
    ) -> CurrentPointer: ...
