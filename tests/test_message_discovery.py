from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.control import MessageDiscoveryQueue
from gmail_ingestion.domain import MessageFetchStatus


def test_discovery_queue_deduplicates_and_reloads(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    run_id = UUID("00000000-0000-0000-0000-000000000301")
    queue = MessageDiscoveryQueue.load_or_create(
        store,
        run_id,
        "acct_0123456789abcdef01234567",
        "100",
    )
    timestamp = datetime(2026, 10, 24, 7, 40, tzinfo=UTC)

    assert queue.merge_page(
        [
            {"id": "m1", "threadId": "t1"},
            {"id": "m2", "threadId": "t2"},
        ],
        page_index=0,
        discovered_at=timestamp,
    ) == 2
    assert queue.merge_page(
        [
            {"id": "m2", "threadId": "t2"},
            {"id": "m3", "threadId": "t3"},
        ],
        page_index=1,
        discovered_at=timestamp,
    ) == 1

    queue.checkpoint_discovery_page("page-3")
    queue.mark_fetched("m1", attempts=1)
    queue.mark_failed(
        "m2",
        attempts=2,
        error_code="GMAIL_API_ERROR",
        error_message="temporary failure",
    )
    queue.persist()

    reloaded = MessageDiscoveryQueue.load_or_create(
        store,
        run_id,
        "acct_0123456789abcdef01234567",
        "999",
    )
    assert reloaded.loaded_existing is True
    assert reloaded.history_start == "100"
    assert reloaded.discovery_complete is False
    assert reloaded.next_page_token == "page-3"
    assert reloaded.pages_scanned == 2
    assert len(reloaded.records) == 3
    assert reloaded.records["m1"].fetch_status is MessageFetchStatus.FETCHED
    assert reloaded.records["m2"].fetch_attempts == 2
