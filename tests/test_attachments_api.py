from typing import Any

from gmail_ingestion.api import GmailAttachmentsApi


class _Request:
    def execute(self) -> dict[str, object]:
        return {"attachmentId": "a1", "size": 3, "data": "YWJj"}


class _Attachments:
    def get(self, **kwargs: Any) -> _Request:
        assert kwargs == {"userId": "me", "messageId": "m1", "id": "a1"}
        return _Request()


class _Messages:
    def attachments(self) -> _Attachments:
        return _Attachments()


class _Users:
    def messages(self) -> _Messages:
        return _Messages()


class _Service:
    def users(self) -> _Users:
        return _Users()


def test_attachments_get_uses_expected_gmail_identifiers() -> None:
    payload = GmailAttachmentsApi(_Service()).get_attachment("m1", "a1")
    assert payload["data"] == "YWJj"
