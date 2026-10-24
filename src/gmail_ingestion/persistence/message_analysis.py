"""Technical JSON artifacts derived from Gmail RAW messages."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from pyingestkit.artifacts.base import ArtifactStore
from pyingestkit.artifacts.naming import safe_component

from gmail_ingestion.domain import AttachmentRecord
from gmail_ingestion.normalization import MimeAnalysis

_JOB_ID = "gmail.messages"


class MessageAnalysisWriter:
    """Persist deterministic MIME/header/body analysis before stable normalization."""

    def __init__(self, store: ArtifactStore, *, job_id: str = _JOB_ID) -> None:
        self.store = store
        self.job_id = job_id

    def write(
        self,
        run_id: UUID,
        account_id: str,
        message_id: str,
        analysis: MimeAnalysis,
        attachments: tuple[AttachmentRecord, ...],
    ) -> str:
        relative_path = f"candidate/mime/{safe_component(message_id)}.json"
        path = self.store.write_json(
            self.job_id,
            run_id,
            relative_path,
            {
                "schema_version": 1,
                "account_id": account_id,
                "message_id": message_id,
                "processed_at": datetime.now(UTC).isoformat(),
                "headers": [
                    {
                        "index": entry.index,
                        "name": entry.name,
                        "value": entry.value,
                        "decoded_value": entry.decoded_value,
                    }
                    for entry in analysis.headers.entries
                ],
                "normalized_headers": {
                    name: list(values)
                    for name, values in analysis.headers.normalized.items()
                },
                "addresses": {
                    role: [
                        {"display_name": address.display_name, "address": address.address}
                        for address in values
                    ]
                    for role, values in analysis.addresses.items()
                },
                "text_body": analysis.text_body,
                "html_body": analysis.html_body,
                "mime_parts": [
                    {
                        "part_id": part.part_id,
                        "part_path": part.part_path,
                        "parent_part_path": part.parent_part_path,
                        "depth": part.depth,
                        "mime_type": part.mime_type,
                        "filename": part.filename,
                        "body_size": part.body_size,
                        "attachment_id": part.attachment_id,
                        "has_inline_data": part.has_inline_data,
                        "is_container": part.is_container,
                        "is_text": part.is_text,
                        "is_html": part.is_html,
                        "is_attachment_candidate": part.is_attachment_candidate,
                        "content_disposition": part.content_disposition,
                        "content_id": part.content_id,
                    }
                    for part in analysis.parts
                ],
                "attachments": [_attachment_payload(item) for item in attachments],
                "warnings": list(analysis.warnings),
            },
        )
        return str(path)


def _attachment_payload(record: AttachmentRecord) -> dict[str, object | None]:
    return {
        "attachment_id": record.attachment_id,
        "part_id": record.part_id,
        "part_path": record.part_path,
        "filename": record.filename,
        "mime_type": record.mime_type,
        "size_bytes": record.size_bytes,
        "content_hash_sha256": record.content_hash_sha256,
        "storage_uri": record.storage_uri,
        "download_status": record.download_status.value,
        "downloaded_at": (
            record.downloaded_at.isoformat() if record.downloaded_at is not None else None
        ),
        "is_inline": record.is_inline,
        "content_id": record.content_id,
        "fetch_attempts": record.fetch_attempts,
        "error_code": record.error_code,
        "error_message": record.error_message,
    }
