"""Immutable storage for Gmail attachment binary content."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from pyingestkit.artifacts.base import ArtifactStore
from pyingestkit.artifacts.naming import safe_component
from pyingestkit.artifacts.raw import RawArtifact
from pyingestkit.core.exceptions import StorageError

from gmail_ingestion.domain import AttachmentCandidate

_JOB_ID = "gmail.attachments"


@dataclass(frozen=True, slots=True)
class ExistingAttachmentBinary:
    """Previously persisted binary recovered during resume."""

    data: bytes
    storage_uri: str


class AttachmentBinaryWriter:
    """Persist attachment bytes separately from analytical artifacts."""

    def __init__(self, store: ArtifactStore, *, job_id: str = _JOB_ID) -> None:
        self.store = store
        self.job_id = job_id

    def read_existing(
        self,
        run_id: UUID,
        message_id: str,
        candidate: AttachmentCandidate,
    ) -> ExistingAttachmentBinary | None:
        relative_path = f"raw/{safe_component(self._name(message_id, candidate))}"
        uri = self.store.uri_for(self.job_id, run_id, relative_path)
        try:
            data = self.store.read_bytes(uri)
        except StorageError:
            return None
        return ExistingAttachmentBinary(data=data, storage_uri=str(uri))

    def write(
        self,
        run_id: UUID,
        message_id: str,
        candidate: AttachmentCandidate,
        data: bytes,
    ) -> RawArtifact:
        """Write attachment content as immutable binary RAW."""
        source_ref = candidate.attachment_id or candidate.part_path
        source_uri = (
            f"gmail://users/me/messages/{message_id}/attachments/{source_ref}"
            if candidate.attachment_id
            else f"gmail://users/me/messages/{message_id}/parts/{candidate.part_path}"
        )
        return self.store.write_raw(
            self.job_id,
            run_id,
            name=self._name(message_id, candidate),
            data=data,
            source_uri=source_uri,
            content_type=candidate.mime_type or "application/octet-stream",
        )

    @staticmethod
    def _name(message_id: str, candidate: AttachmentCandidate) -> str:
        source_ref = candidate.attachment_id or candidate.part_path
        filename = candidate.filename or "binary"
        return f"attachment-{message_id}-{source_ref}-{filename}"
