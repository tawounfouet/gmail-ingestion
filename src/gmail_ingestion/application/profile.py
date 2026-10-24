"""Retrieve and verify the current Gmail mailbox profile."""

from __future__ import annotations

from collections.abc import Callable

from gmail_ingestion.api import GmailApiClient, map_gmail_profile
from gmail_ingestion.auth.provider import GmailOAuthProvider
from gmail_ingestion.domain import GmailAccount

ClientFactory = Callable[..., GmailApiClient]


class GetMailboxProfile:
    """Load reusable credentials, refresh if required, and fetch users.getProfile."""

    def __init__(
        self,
        provider: GmailOAuthProvider,
        *,
        client_factory: ClientFactory = GmailApiClient,
    ) -> None:
        self.provider = provider
        self.client_factory = client_factory

    def run(self, account_id: str | None = None) -> GmailAccount:
        resolved, credentials = self.provider.load(account_id)
        client = self.client_factory(credentials=credentials)
        account = map_gmail_profile(resolved, client.get_profile())
        self.provider.store.verify_account_email(resolved, account.email_address)
        self.provider.store.save_account(account)
        self.provider.store.set_active_account(resolved)
        return account
