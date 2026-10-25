"""Persistence for incremental reducer effects."""

from __future__ import annotations

from pyingestkit.artifacts.base import ArtifactStore

from gmail_ingestion.sync import MessageLabelMutation, MessageTombstone

_HISTORY_JOB_ID = "gmail.history"


class IncrementalMutationWriter:
    """Persist deterministic tombstone and label-relation mutation artifacts."""

    def __init__(self, store: ArtifactStore, *, job_id: str = _HISTORY_JOB_ID) -> None:
        self.store = store
        self.job_id = job_id

    def write_tombstone(self, tombstone: MessageTombstone) -> str:
        path = self.store.write_json(
            self.job_id,
            tombstone.run_id,
            f"message_tombstones/{tombstone.source_event_id}.json",
            tombstone.to_payload(),
        )
        return str(path)

    def write_label_mutation(self, mutation: MessageLabelMutation) -> str:
        path = self.store.write_json(
            self.job_id,
            mutation.run_id,
            f"message_label_mutations/{mutation.source_event_id}.json",
            mutation.to_payload(),
        )
        return str(path)
