from typing import Any

from gmail_ingestion.api.messages import GmailMessagesApi


class _Request:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def execute(self) -> dict[str, object]:
        return self.payload


class _Messages:
    def list(self, **kwargs: Any) -> _Request:
        assert kwargs["userId"] == "me"
        assert kwargs["maxResults"] == 100
        assert kwargs["includeSpamTrash"] is True
        assert kwargs["pageToken"] == "next"
        return _Request(
            {
                "messages": [{"id": "m1", "threadId": "t1"}],
                "nextPageToken": "later",
            }
        )

    def get(self, **kwargs: Any) -> _Request:
        assert kwargs == {"userId": "me", "id": "m1", "format": "full"}
        return _Request({"id": "m1", "threadId": "t1", "payload": {}})


class _Users:
    def messages(self) -> _Messages:
        return _Messages()


class _Service:
    def users(self) -> _Users:
        return _Users()


def test_messages_list_page_and_full_get() -> None:
    api = GmailMessagesApi(_Service())
    page = api.list_messages(
        page_token="next",
        max_results=100,
        include_spam_trash=True,
    )
    assert page["nextPageToken"] == "later"
    assert api.get_message("m1", format="full")["id"] == "m1"
