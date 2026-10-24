import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.manifest import GmailRunManifest, ManifestWriter


def test_manifest_is_json_serializable_and_resume_settings_are_guarded(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    run_id = UUID("00000000-0000-0000-0000-000000000401")
    started = datetime(2026, 10, 24, 8, 0, tzinfo=UTC)
    writer = ManifestWriter(store)
    manifest = GmailRunManifest(
        run_id=run_id,
        account_id="acct_0123456789abcdef01234567",
        started_at=started,
        completed_at=None,
        status="RUNNING",
        history_start="123",
        include_spam_trash=False,
        download_attachments=False,
        max_attachment_bytes=None,
        messages_discovered=2,
        messages_fetched=1,
        messages_failed=0,
    )

    path = Path(writer.write(manifest))
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["schema_version"] == 1
    assert payload["message_format"] == "full"
    assert payload["candidate_checkpoint"] == "123"
    assert writer.existing_started_at(run_id) == started
    writer.validate_resume_settings(
        run_id,
        include_spam_trash=False,
        download_attachments=False,
        max_attachment_bytes=None,
    )
