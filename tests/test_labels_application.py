from pathlib import Path
from typing import Any
from uuid import UUID

from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.application import IngestLabels
from gmail_ingestion.auth.store import LocalCredentialStore
from gmail_ingestion.domain import GmailAccount


class _Credentials:
    valid = True
    expired = False
    refresh_token = "refresh"

    def refresh(self, request: Any) -> None:
        raise AssertionError("refresh should not be called")

    def to_json(self) -> str:
        return '{"token":"secret","refresh_token":"refresh"}'


class _Provider:
    def __init__(self, root: Path) -> None:
        self.store = LocalCredentialStore(root)
        self.credentials = _Credentials()
        self.account_id = self.store.find_or_create_account_id("user@example.com")
        self.store.save_account(
            GmailAccount(self.account_id, "user@example.com", 10, 5, "100")
        )
        self.store.set_active_account(self.account_id)

    def load(self, account_id: str | None = None) -> tuple[str, _Credentials]:
        return self.store.resolve_account_id(account_id), self.credentials


class _Labels:
    def list_labels(self) -> dict[str, object]:
        return {
            "labels": [
                {"id": "INBOX", "name": "INBOX", "type": "system"},
                {"id": "Label_1", "name": "Clients", "type": "user"},
            ]
        }

    def get_label(self, label_id: str) -> dict[str, object]:
        if label_id == "INBOX":
            return {
                "id": "INBOX",
                "name": "INBOX",
                "type": "system",
                "messagesTotal": 10,
            }
        return {
            "id": "Label_1",
            "name": "Clients",
            "type": "user",
            "messagesTotal": 2,
        }


class _Client:
    def __init__(self) -> None:
        self.labels = _Labels()

    def get_profile(self) -> dict[str, object]:
        return {
            "emailAddress": "user@example.com",
            "messagesTotal": 10,
            "threadsTotal": 5,
            "historyId": "100",
        }


def _factory(**kwargs: object) -> _Client:
    assert "credentials" in kwargs
    return _Client()


def test_ingest_labels_vertical_slice(tmp_path: Path) -> None:
    provider = _Provider(tmp_path / "credentials")
    run_id = UUID("00000000-0000-0000-0000-000000000123")
    result = IngestLabels(  # type: ignore[arg-type]
        provider,
        LocalArtifactStore(tmp_path / "artifacts"),
        client_factory=_factory,
    ).run(run_id=run_id)

    assert result.run_id == run_id
    assert result.account_id == provider.account_id
    assert result.label_count == 2
    assert result.raw_artifact_count == 3
    assert result.parquet_path.is_file()
    assert result.schema_path.is_file()
