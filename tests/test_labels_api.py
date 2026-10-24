from gmail_ingestion.api.labels import GmailLabelsApi


class _Request:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def execute(self) -> dict[str, object]:
        return self.payload


class _Labels:
    def list(self, *, userId: str) -> _Request:  # noqa: N802
        assert userId == "me"
        return _Request({"labels": [{"id": "INBOX", "name": "INBOX", "type": "system"}]})

    def get(self, *, userId: str, id: str) -> _Request:  # noqa: A002, N802
        assert userId == "me"
        assert id == "INBOX"
        return _Request(
            {
                "id": "INBOX",
                "name": "INBOX",
                "type": "system",
                "messagesTotal": 12,
            }
        )


class _Users:
    def labels(self) -> _Labels:
        return _Labels()


class _Service:
    def users(self) -> _Users:
        return _Users()


def test_labels_list_and_get_use_authenticated_user() -> None:
    api = GmailLabelsApi(_Service())
    assert api.list_labels()["labels"][0]["id"] == "INBOX"  # type: ignore[index]
    assert api.get_label("INBOX")["messagesTotal"] == 12
