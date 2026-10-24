import json
import os
from pathlib import Path

import pytest

from gmail_ingestion.auth.store import AccountIdentityMismatchError, LocalCredentialStore
from gmail_ingestion.domain import GmailAccount


def test_store_reuses_account_id_and_private_token(tmp_path: Path) -> None:
    store = LocalCredentialStore(tmp_path / "credentials")
    account_id = store.find_or_create_account_id("User@Example.com")
    account = GmailAccount(account_id, "user@example.com", 1, 1, "10")
    store.save_account(account)
    store.save_credentials_json(account_id, json.dumps({"token": "secret", "refresh_token": "r"}))
    store.set_active_account(account_id)

    assert store.find_or_create_account_id("user@example.com") == account_id
    resolved, payload = store.load_credentials_info()
    assert resolved == account_id
    assert payload["refresh_token"] == "r"

    if os.name == "posix":
        token_path = tmp_path / "credentials" / "accounts" / account_id / "token.json"
        assert token_path.stat().st_mode & 0o777 == 0o600


def test_store_rejects_cross_account_identity(tmp_path: Path) -> None:
    store = LocalCredentialStore(tmp_path / "credentials")
    account_id = store.find_or_create_account_id("first@example.com")
    store.save_account(GmailAccount(account_id, "first@example.com", None, None, None))

    with pytest.raises(AccountIdentityMismatchError):
        store.verify_account_email(account_id, "other@example.com")
