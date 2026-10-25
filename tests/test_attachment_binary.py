from __future__ import annotations

from pathlib import Path
from uuid import UUID

from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.domain import AttachmentCandidate
from gmail_ingestion.persistence.attachment_binary import AttachmentBinaryWriter


def _candidate(*, attachment_id: str, filename: str) -> AttachmentCandidate:
    return AttachmentCandidate(
        attachment_id=attachment_id,
        part_id="1",
        part_path="root/1",
        filename=filename,
        mime_type="text/calendar",
        size_bytes=5,
        is_inline=False,
        content_id=None,
    )


def test_long_gmail_attachment_id_uses_short_deterministic_filename(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    writer = AttachmentBinaryWriter(store)
    run_id = UUID("00000000-0000-0000-0000-000000000401")
    message_id = "1a16ab890c9069b4"
    attachment_id = "ANGjdJ9" + ("X" * 600)
    candidate = _candidate(attachment_id=attachment_id, filename="invite.ics")

    first = writer.write(run_id, message_id, candidate, b"ICAL")
    recovered = writer.read_existing(run_id, message_id, candidate)

    raw_dir = store.run_root("gmail.attachments", run_id) / "raw"
    files = list(raw_dir.iterdir())

    assert len(files) == 1
    assert files[0].read_bytes() == b"ICAL"
    assert files[0].suffix == ".ics"
    assert len(files[0].name) < 100
    assert attachment_id not in files[0].name
    assert recovered is not None
    assert recovered.data == b"ICAL"
    assert str(first.storage_uri) == recovered.storage_uri


def test_untrusted_or_excessive_extension_is_dropped(tmp_path: Path) -> None:
    writer = AttachmentBinaryWriter(LocalArtifactStore(tmp_path / "artifacts"))
    candidate = _candidate(
        attachment_id="att-1",
        filename="file." + ("x" * 100),
    )

    name = writer._name("message-1", candidate)

    assert len(name) < 100
    assert not name.endswith("." + ("x" * 100))
