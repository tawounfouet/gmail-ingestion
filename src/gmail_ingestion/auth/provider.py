"""OAuth provider for local Gmail Desktop authorization."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any, Protocol, cast

from gmail_ingestion.config import OAuthSettings

from .store import LocalCredentialStore


class GmailAuthError(RuntimeError):
    """Base authentication error surfaced without OAuth token material."""


class ReauthorizationRequiredError(GmailAuthError):
    """Raised when stored credentials can no longer be refreshed."""


class RefreshTokenMissingError(GmailAuthError):
    """Raised when an interactive grant does not include a refresh token."""


class CredentialLike(Protocol):
    """Minimal Google credential surface consumed by the connector."""

    valid: bool
    expired: bool
    refresh_token: str | None

    def refresh(self, request: Any) -> None: ...

    def to_json(self) -> str: ...


class GmailOAuthProvider:
    """Authorize, persist, load, and transparently refresh Gmail credentials."""

    def __init__(self, settings: OAuthSettings, store: LocalCredentialStore) -> None:
        self.settings = settings
        self.store = store

    def authorize_interactive(self) -> CredentialLike:
        """Run Google's installed-app browser flow with the read-only Gmail scope."""
        secrets_path = self.settings.client_secrets_file.expanduser()
        if not secrets_path.is_file():
            raise GmailAuthError(
                f"OAuth client file not found: {secrets_path}. "
                "Create a Google OAuth Desktop client and download its JSON credentials."
            )

        flow_module = importlib.import_module("google_auth_oauthlib.flow")
        flow_class = cast(Any, flow_module.InstalledAppFlow)
        flow = flow_class.from_client_secrets_file(
            str(secrets_path),
            list(self.settings.scopes),
        )
        credentials = cast(
            CredentialLike,
            flow.run_local_server(
                host=self.settings.local_server_host,
                port=self.settings.local_server_port,
                open_browser=self.settings.open_browser,
                access_type="offline",
                prompt="consent",
            ),
        )
        if not credentials.refresh_token:
            raise RefreshTokenMissingError(
                "Google did not return a refresh token; re-authorize the account with consent."
            )
        return credentials

    def load(self, account_id: str | None = None) -> tuple[str, CredentialLike]:
        """Load the requested credentials and refresh them when expired."""
        resolved, payload = self.store.load_credentials_info(account_id)
        credentials_module = importlib.import_module("google.oauth2.credentials")
        credentials_class = cast(Any, credentials_module.Credentials)
        credentials = cast(
            CredentialLike,
            credentials_class.from_authorized_user_info(payload, list(self.settings.scopes)),
        )

        if credentials.valid:
            return resolved, credentials

        if credentials.expired and credentials.refresh_token:
            request_module = importlib.import_module("google.auth.transport.requests")
            request = cast(Any, request_module.Request)()
            try:
                credentials.refresh(request)
            except Exception as exc:  # noqa: BLE001 - sanitize third-party refresh failures
                raise ReauthorizationRequiredError(
                    f"Stored credentials for {resolved} can no longer be refreshed; "
                    "run `gmail-ingest auth` again."
                ) from exc
            self.persist(resolved, credentials)
            return resolved, credentials

        raise ReauthorizationRequiredError(
            f"Stored credentials for {resolved} are not reusable; run `gmail-ingest auth` again."
        )

    def persist(self, account_id: str, credentials: CredentialLike) -> None:
        """Persist credentials without logging or exposing their serialized value."""
        self.store.save_credentials_json(account_id, credentials.to_json())

    def revoke_local(self, account_id: str) -> None:
        """Remove local credentials. Remote Google revocation is a later explicit capability."""
        self.store.delete_credentials(account_id)


def build_provider(
    client_secrets_file: Path,
    credential_dir: Path,
    *,
    open_browser: bool = True,
) -> GmailOAuthProvider:
    """Build the local OAuth provider used by CLI commands."""
    settings = OAuthSettings(
        client_secrets_file=client_secrets_file,
        credential_dir=credential_dir,
        open_browser=open_browser,
    )
    store = LocalCredentialStore(settings.credential_dir)
    return GmailOAuthProvider(settings, store)
