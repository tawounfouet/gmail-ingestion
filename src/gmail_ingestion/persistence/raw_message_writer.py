"""Immutable RAW persistence for hydrated Gmail messages."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

from pyingestkit.artifacts.base import ArtifactStore
from pyingestkit.artifacts.naming import safe_component
from pyingestkit.artifacts.raw import RawArtifact
from pyingestkit.core.exceptions import StorageError

_JOB_ID = "gmail.messages"


class RawMessageWriter:
    """Persist one users.messages.get(full) response as immutable RAW JSON."""

    def __init__(self, store: ArtifactStore, *, job_id: str = _JOB_ID) -> None:
        self.store = store
        self.job_id = job_id

    def exists(self, run_id: UUID, message_id: str) -> bool:
        """Return whether immutable RAW already exists for this run/message."""
        try:
            self.read(run_id, message_id)
        except (StorageError, ValueError):
            return False
        return True

    def read(self, run_id: UUID, message_id: str) -> Mapping[str, Any]:
        """Read and validate one persisted RAW message JSON object."""
        relative_path = f"raw/{safe_component(self._name(message_id))}"
        raw = self.store.read_bytes(self.store.uri_for(self.job_id, run_id, relative_path))
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"RAW Gmail message {message_id!r} is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"RAW Gmail message {message_id!r} must be a JSON object")
        if payload.get("id") != message_id:
            raise ValueError(f"RAW Gmail message {message_id!r} has an inconsistent source id")
        return cast(Mapping[str, Any], payload)

    def write(
        self,
        run_id: UUID,
        message_id: str,
        payload: Mapping[str, Any],
    ) -> RawArtifact:
        """Write create-once JSON through the PyIngestKit ArtifactStore."""
        source_id = payload.get("id")
        if source_id != message_id:
            raise ValueError("Hydrated Gmail message id does not match requested message_id")
        data = (
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
        ).encode("utf-8")
        return self.store.write_raw(
            self.job_id,
            run_id,
            name=self._name(message_id),
            data=data,
            source_uri=f"gmail://users/me/messages/{message_id}?format=full",
            content_type="application/json",
        )

    @staticmethod
    def _name(message_id: str) -> str:
        if not message_id:
            raise ValueError("message_id must not be empty")
        return f"message-{message_id}-full.json"
