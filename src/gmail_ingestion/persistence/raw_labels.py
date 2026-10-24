"""RAW persistence for Gmail label responses."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from pyingestkit.artifacts.base import ArtifactStore
from pyingestkit.artifacts.raw import RawArtifact


class RawLabelsWriter:
    """Persist source responses through PyIngestKit's immutable RAW contract."""

    def __init__(self, store: ArtifactStore, *, job_id: str = "gmail.labels") -> None:
        self.store = store
        self.job_id = job_id

    def write(
        self,
        run_id: UUID,
        list_payload: Mapping[str, Any],
        detail_payloads: Sequence[Mapping[str, Any]],
    ) -> tuple[RawArtifact, ...]:
        """Persist labels.list and labels.get payloads as immutable JSON RAW."""
        artifacts = [
            self.store.write_raw(
                self.job_id,
                run_id,
                name="labels-list.json",
                data=_json_bytes(list_payload),
                source_uri="gmail://users/me/labels",
                content_type="application/json",
            )
        ]
        for payload in detail_payloads:
            label_id = payload.get("id")
            if not isinstance(label_id, str) or not label_id:
                continue
            artifacts.append(
                self.store.write_raw(
                    self.job_id,
                    run_id,
                    name=f"label-{label_id}.json",
                    data=_json_bytes(payload),
                    source_uri=f"gmail://users/me/labels/{label_id}",
                    content_type="application/json",
                )
            )
        return tuple(artifacts)


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n"
    ).encode("utf-8")
