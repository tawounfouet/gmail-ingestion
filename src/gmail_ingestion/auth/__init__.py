"""Authentication and credential persistence."""

from .provider import (
    GmailAuthError,
    GmailOAuthProvider,
    ReauthorizationRequiredError,
    RefreshTokenMissingError,
    build_provider,
)
from .store import (
    AccountIdentityMismatchError,
    CredentialNotFoundError,
    CredentialStoreError,
    LocalCredentialStore,
)

__all__ = [
    "AccountIdentityMismatchError",
    "CredentialNotFoundError",
    "CredentialStoreError",
    "GmailAuthError",
    "GmailOAuthProvider",
    "LocalCredentialStore",
    "ReauthorizationRequiredError",
    "RefreshTokenMissingError",
    "build_provider",
]
