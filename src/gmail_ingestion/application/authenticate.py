"""Authenticate a mailbox and establish its local account context."""

from __future__ import annotations

from collections.abc import Callable

from gmail_ingestion.api import GmailApiClient, map_gmail_profile
from gmail_ingestion.auth.provider import GmailOAuthProvider
from gmail_ingestion.domain import GmailAccount

ClientFactory = Callable[..., GmailApiClient]


class AuthenticateMailbox:
    """Run interactive OAuth, verify Gmail identity, and persist credentials."""

    def __init__(
        self,
        provider: GmailOAuthProvider,
        *,
        client_factory: ClientFactory = GmailApiClient,
    ) -> None:
        self.provider = provider
        self.client_factory = client_factory

    def run(self) -> GmailAccount:
        credentials = self.provider.authorize_interactive()
        client = self.client_factory(credentials=credentials)
        payload = client.get_profile()
        provisional = map_gmail_profile("acct_000000000000000000000000", payload)
        account_id = self.provider.store.find_or_create_account_id(provisional.email_address)
        account = map_gmail_profile(account_id, payload)
        self.provider.store.verify_account_email(account_id, account.email_address)
        self.provider.persist(account_id, credentials)
        self.provider.store.save_account(account)
        self.provider.store.set_active_account(account_id)
        return account
