from typing import Any

import pytest

from gmail_ingestion.api import GmailHistoryApi, GmailHistoryExpiredError


class _Request:
    def __init__(self, payload: dict[str, object] | Exception) -> None:
        self.payload = payload

    def execute(self) -> dict[str, object]:
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class _History:
    def __init__(self, payload: dict[str, object] | Exception) -> None:
        self.payload = payload
        self.calls: list[dict[str, Any]] = []

    def list(self, **kwargs: Any) -> _Request:
        self.calls.append(kwargs)
        return _Request(self.payload)


class _Users:
    def __init__(self, history: _History) -> None:
        self._history = history

    def history(self) -> _History:
        return self._history


class _Service:
    def __init__(self, history: _History) -> None:
        self._users = _Users(history)

    def users(self) -> _Users:
        return self._users


class _Response:
    status = 404


class _Http404(Exception):
    def __init__(self) -> None:
        self.resp = _Response()


def test_history_list_forwards_cursor_pagination_and_message_added_filter() -> None:
    history = _History(
        {
            "history": [{"id": "101", "messagesAdded": []}],
            "historyId": "125",
            "nextPageToken": "p2",
        }
    )
    api = GmailHistoryApi(_Service(history))

    page = api.list_history(
        start_history_id="100",
        page_token="p1",
        max_results=200,
        history_types=("messageAdded",),
    )

    assert page["historyId"] == "125"
    assert history.calls == [
        {
            "userId": "me",
            "startHistoryId": "100",
            "maxResults": 200,
            "pageToken": "p1",
            "historyTypes": ["messageAdded"],
        }
    ]


def test_history_list_maps_http_404_to_expired_checkpoint() -> None:
    api = GmailHistoryApi(_Service(_History(_Http404())))

    with pytest.raises(GmailHistoryExpiredError, match="full sync"):
        api.list_history(start_history_id="100", history_types=("messageAdded",))


def test_history_list_rejects_unknown_history_type() -> None:
    api = GmailHistoryApi(_Service(_History({})))

    with pytest.raises(ValueError, match="Unsupported"):
        api.list_history(start_history_id="100", history_types=("unknown",))
