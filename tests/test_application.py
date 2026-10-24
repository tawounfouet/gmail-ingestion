from pathlib import Path
from typing import Any

from gmail_ingestion.application import AuthenticateMailbox, GetMailboxProfile
from gmail_ingestion.auth.store import LocalCredentialStore


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
        self.persisted: list[str] = []

    def authorize_interactive(self) -> _Credentials:
        return self.credentials

    def persist(self, account_id: str, credentials: _Credentials) -> None:
        assert credentials is self.credentials
        self.persisted.append(account_id)
        self.store.save_credentials_json(account_id, credentials.to_json())

    def load(self, account_id: str | None = None) -> tuple[str, _Credentials]:
        return self.store.resolve_account_id(account_id), self.credentials


class _Client:
    def get_profile(self) -> dict[str, object]:
        return {
            "emailAddress": "user@example.com",
            "messagesTotal": 9,
            "threadsTotal": 4,
            "historyId": "123",
        }


def _factory(**kwargs: object) -> _Client:
    assert "credentials" in kwargs
    return _Client()


def test_authenticate_and_then_get_profile(tmp_path: Path) -> None:
    provider = _Provider(tmp_path / "credentials")
    authenticated = AuthenticateMailbox(  # type: ignore[arg-type]
        provider, client_factory=_factory
    ).run()
    assert authenticated.account_id.startswith("acct_")
    assert provider.persisted == [authenticated.account_id]

    fetched = GetMailboxProfile(provider, client_factory=_factory).run()  # type: ignore[arg-type]
    assert fetched == authenticated
