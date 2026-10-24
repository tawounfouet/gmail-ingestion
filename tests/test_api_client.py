from gmail_ingestion.api import GmailApiClient


class _Request:
    def execute(self) -> dict[str, object]:
        return {"emailAddress": "user@example.com", "historyId": "42"}


class _Users:
    def getProfile(self, *, userId: str) -> _Request:  # noqa: N802
        assert userId == "me"
        return _Request()


class _Service:
    def users(self) -> _Users:
        return _Users()


def test_get_profile_uses_authenticated_user() -> None:
    client = GmailApiClient(service=_Service())
    assert client.get_profile()["emailAddress"] == "user@example.com"
