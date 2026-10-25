from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from gmail_ingestion.domain import map_history_events
from gmail_ingestion.persistence import LocalHistoryReceiptStore

ACCOUNT_ID = "acct_0123456789abcdef01234567"
RUN_A = UUID("00000000-0000-0000-0000-000000000601")
RUN_B = UUID("00000000-0000-0000-0000-000000000602")


def _event(run_id: UUID):
    return map_history_events(
        account_id=ACCOUNT_ID,
        run_id=run_id,
        page={
            "history": [
                {
                    "id": "701",
                    "labelsRemoved": [
                        {
                            "message": {"id": "m1", "threadId": "t1"},
                            "labelIds": ["INBOX"],
                        }
                    ],
                }
            ]
        },
        observed_at=datetime(2026, 10, 25, 14, 0, tzinfo=UTC),
        source_page_token=None,
    )[0]


def test_receipt_is_durable_and_idempotent_across_runs(tmp_path: Path) -> None:
    store = LocalHistoryReceiptStore(tmp_path)
    first_event = _event(RUN_A)
    second_run_same_logical_event = _event(RUN_B)

    first = store.mark_applied(
        first_event,
        applied_at=datetime(2026, 10, 25, 14, 1, tzinfo=UTC),
    )
    loaded = store.load(ACCOUNT_ID, first_event.event_id)
    replay = store.mark_applied(second_run_same_logical_event)

    assert loaded == first
    assert replay == first
    assert replay.source_run_id == RUN_A
    assert replay.event_id == second_run_same_logical_event.event_id
