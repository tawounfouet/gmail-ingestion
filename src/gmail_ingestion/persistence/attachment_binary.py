"""Immutable storage for Gmail attachment binary content."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import PurePath
from uuid import UUID

from pyingestkit.artifacts.base import ArtifactStore
from pyingestkit.artifacts.naming import safe_component
from pyingestkit.artifacts.raw import RawArtifact
from pyingestkit.core.exceptions import StorageError

from gmail_ingestion.domain import AttachmentCandidate

_JOB_ID = "gmail.attachments"
_SAFE_EXTENSION = re.compile(r"^\.[A-Za-z0-9]{1,15}$")


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
        """Return a short deterministic physical filename.

        Gmail attachment IDs can be hundreds of characters long. They remain available in
        attachment metadata/source URIs but must never be copied verbatim into filesystem names.
        """
        source_ref = candidate.attachment_id or candidate.part_path
        message_key = sha256(message_id.encode("utf-8")).hexdigest()[:16]
        source_key = sha256(source_ref.encode("utf-8")).hexdigest()[:32]
        extension = _safe_extension(candidate.filename)
        return f"attachment-{message_key}-{source_key}{extension}"


def _safe_extension(filename: str) -> str:
    """Preserve a short conventional extension without trusting the original filename."""
    if not filename:
        return ""
    suffix = PurePath(filename).suffix.lower()
    return suffix if _SAFE_EXTENSION.fullmatch(suffix) else ""
