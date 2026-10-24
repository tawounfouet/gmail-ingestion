"""Private local storage for Gmail OAuth credentials and account metadata."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast

from gmail_ingestion.domain import GmailAccount

_ACCOUNT_ID_RE = re.compile(r"^acct_[0-9a-f]{24}$")


class CredentialStoreError(RuntimeError):
    """Base error for local credential persistence."""


class CredentialNotFoundError(CredentialStoreError):
    """Raised when no credentials exist for the requested account."""


class AccountIdentityMismatchError(CredentialStoreError):
    """Raised when Gmail returns a different mailbox for stored credentials."""


class LocalCredentialStore:
    """Store account metadata and OAuth tokens in a private per-account namespace."""

    def __init__(self, root: Path) -> None:
        self.root = root.expanduser()
        self.accounts_dir = self.root / "accounts"
        self.active_account_file = self.root / "active_account"
        self._ensure_private_dir(self.root)
        self._ensure_private_dir(self.accounts_dir)

    def resolve_account_id(self, account_id: str | None = None) -> str:
        """Resolve an explicit account ID or the currently active account."""
        if account_id is not None:
            return self._validate_account_id(account_id)
        if not self.active_account_file.exists():
            raise CredentialNotFoundError(
                "No active Gmail account. Run `gmail-ingest auth` first."
            )
        resolved = self.active_account_file.read_text(encoding="utf-8").strip()
        return self._validate_account_id(resolved)

    def find_or_create_account_id(self, email_address: str) -> str:
        """Return a stable opaque account ID for a mailbox known to this store."""
        normalized = email_address.strip().lower()
        for metadata_file in self.accounts_dir.glob("acct_*/account.json"):
            try:
                payload = self._read_json(metadata_file)
            except (OSError, json.JSONDecodeError, CredentialStoreError):
                continue
            stored_email = str(payload.get("email_address", "")).strip().lower()
            if stored_email == normalized:
                return self._validate_account_id(metadata_file.parent.name)
        return f"acct_{uuid.uuid4().hex[:24]}"

    def save_credentials_json(self, account_id: str, credential_json: str) -> None:
        """Persist serialized OAuth credentials with owner-only file permissions."""
        account_dir = self._account_dir(account_id)
        self._ensure_private_dir(account_dir)
        try:
            payload = json.loads(credential_json)
        except json.JSONDecodeError as exc:
            raise CredentialStoreError("Credential payload is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise CredentialStoreError("Credential payload must be a JSON object")
        self._write_private_json(account_dir / "token.json", payload)

    def load_credentials_info(self, account_id: str | None = None) -> tuple[str, dict[str, Any]]:
        """Load the serialized credentials for an explicit or active account."""
        resolved = self.resolve_account_id(account_id)
        path = self._account_dir(resolved) / "token.json"
        if not path.exists():
            raise CredentialNotFoundError(f"No OAuth token stored for {resolved}")
        return resolved, self._read_json(path)

    def save_account(self, account: GmailAccount) -> None:
        """Persist non-secret mailbox metadata in the account namespace."""
        account_dir = self._account_dir(account.account_id)
        self._ensure_private_dir(account_dir)
        self._write_private_json(account_dir / "account.json", asdict(account))

    def verify_account_email(self, account_id: str, email_address: str) -> None:
        """Reject cross-account credential use when stored metadata disagrees."""
        path = self._account_dir(account_id) / "account.json"
        if not path.exists():
            return
        payload = self._read_json(path)
        expected = str(payload.get("email_address", "")).strip().lower()
        actual = email_address.strip().lower()
        if expected and expected != actual:
            raise AccountIdentityMismatchError(
                f"Stored account {account_id} does not match the authenticated Gmail mailbox"
            )

    def set_active_account(self, account_id: str) -> None:
        """Set the default account without exposing its email address in the pointer."""
        resolved = self._validate_account_id(account_id)
        self._write_private_text(self.active_account_file, f"{resolved}\n")

    def delete_credentials(self, account_id: str) -> None:
        """Delete only the local OAuth token for a disconnected account."""
        path = self._account_dir(account_id) / "token.json"
        if path.exists():
            path.unlink()

    def _account_dir(self, account_id: str) -> Path:
        return self.accounts_dir / self._validate_account_id(account_id)

    @staticmethod
    def _validate_account_id(account_id: str) -> str:
        if not _ACCOUNT_ID_RE.fullmatch(account_id):
            raise CredentialStoreError("Invalid account_id format")
        return account_id

    @staticmethod
    def _ensure_private_dir(path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            path.chmod(0o700)
        except OSError:
            pass

    def _write_private_json(self, path: Path, payload: dict[str, Any]) -> None:
        self._write_private_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    @staticmethod
    def _write_private_text(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
            try:
                path.chmod(0o600)
            except OSError:
                pass
        finally:
            if temp.exists():
                temp.unlink()

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise CredentialStoreError(f"Expected a JSON object in {path.name}")
        return cast(dict[str, Any], payload)
