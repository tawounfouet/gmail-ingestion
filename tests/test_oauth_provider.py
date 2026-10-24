import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gmail_ingestion.auth.provider import GmailOAuthProvider
from gmail_ingestion.auth.store import LocalCredentialStore
from gmail_ingestion.config import GMAIL_READONLY_SCOPE, OAuthSettings


class _RefreshingCredentials:
    valid = False
    expired = True
    refresh_token = "refresh-token"

    def __init__(self) -> None:
        self.refreshed = False

    def refresh(self, request: Any) -> None:
        self.refreshed = True
        self.valid = True
        self.expired = False

    def to_json(self) -> str:
        return json.dumps(
            {
                "token": "new-access-token",
                "refresh_token": self.refresh_token,
                "client_id": "client-id",
                "client_secret": "client-secret",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": [GMAIL_READONLY_SCOPE],
            }
        )


def test_oauth_settings_reject_scope_escalation() -> None:
    with pytest.raises(ValueError, match="gmail.readonly"):
        OAuthSettings(scopes=("https://www.googleapis.com/auth/gmail.modify",))


def test_load_refreshes_and_persists_expired_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = LocalCredentialStore(tmp_path / "credentials")
    account_id = store.find_or_create_account_id("user@example.com")
    store.save_credentials_json(
        account_id,
        json.dumps(
            {
                "token": "old",
                "refresh_token": "refresh-token",
                "client_id": "client-id",
                "client_secret": "client-secret",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": [GMAIL_READONLY_SCOPE],
            }
        ),
    )
    store.set_active_account(account_id)
    credentials = _RefreshingCredentials()

    class _CredentialsClass:
        @staticmethod
        def from_authorized_user_info(
            payload: dict[str, Any], scopes: list[str]
        ) -> _RefreshingCredentials:
            assert payload["refresh_token"] == "refresh-token"
            assert scopes == [GMAIL_READONLY_SCOPE]
            return credentials

    def fake_import(name: str) -> Any:
        if name == "google.oauth2.credentials":
            return SimpleNamespace(Credentials=_CredentialsClass)
        if name == "google.auth.transport.requests":
            return SimpleNamespace(Request=lambda: object())
        raise AssertionError(name)

    monkeypatch.setattr("gmail_ingestion.auth.provider.importlib.import_module", fake_import)
    provider = GmailOAuthProvider(OAuthSettings(credential_dir=store.root), store)

    resolved, loaded = provider.load()

    assert resolved == account_id
    assert loaded is credentials
    assert credentials.refreshed is True
    _, saved = store.load_credentials_info(account_id)
    assert saved["token"] == "new-access-token"
