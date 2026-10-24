import pytest

from gmail_ingestion.api.profile import GmailProfileError, map_gmail_profile


def test_map_profile() -> None:
    account = map_gmail_profile(
        "acct_0123456789abcdef01234567",
        {
            "emailAddress": "user@example.com",
            "messagesTotal": 12,
            "threadsTotal": "7",
            "historyId": "999",
        },
    )
    assert account.email_address == "user@example.com"
    assert account.messages_total == 12
    assert account.threads_total == 7
    assert account.history_id == "999"


def test_map_profile_requires_email() -> None:
    with pytest.raises(GmailProfileError, match="emailAddress"):
        map_gmail_profile("acct_0123456789abcdef01234567", {})
