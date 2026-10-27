from __future__ import annotations

import base64
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.application import (
    LegacyBaselineBootstrapError,
    LegacyRawBaselineBootstrapper,
)
from gmail_ingestion.control import MessageDiscoveryQueue
from gmail_ingestion.persistence import NormalizedBaselineReader, RawMessageWriter

ACCOUNT_ID = "acct_0123456789abcdef01234567"
RUN_ID = UUID("00000000-0000-0000-0000-000000000901")
T0 = datetime(2026, 10, 25, 21, 0, tzinfo=UTC)


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _message(message_id: str, thread_id: str, subject: str) -> dict[str, object]:
    return {
        "id": message_id,
        "threadId": thread_id,
        "historyId": "100",
        "internalDate": "1000",
        "snippet": subject,
        "sizeEstimate": 10,
        "labelIds": ["INBOX"],
        "payload": {
            "partId": "",
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Sender <sender@example.com>"},
                {"name": "To", "value": "User <user@example.com>"},
                {"name": "Subject", "value": subject},
            ],
            "body": {"size": len(subject), "data": _b64(subject)},
        },
    }


def _seed_snapshot(root: Path) -> LocalArtifactStore:
    store = LocalArtifactStore(root)
    queue = MessageDiscoveryQueue(
        store,
        RUN_ID,
        ACCOUNT_ID,
        "100",
    )
    queue.merge_page(
        [
            {"id": "m1", "threadId": "t1"},
            {"id": "m2", "threadId": "t2"},
        ],
        page_index=0,
        discovered_at=T0,
    )
    queue.mark_discovery_complete()
    writer = RawMessageWriter(store)
    writer.write(RUN_ID, "m1", _message("m1", "t1", "one"))
    writer.write(RUN_ID, "m2", _message("m2", "t2", "two"))
    queue.mark_fetched("m1", attempts=1)
    queue.mark_fetched("m2", attempts=1)
    queue.persist()
    return store


def test_bootstrap_materializes_projection_baseline_from_legacy_raw(tmp_path: Path) -> None:
    store = _seed_snapshot(tmp_path / "artifacts")
    progress: list[tuple[int, int]] = []

    result = LegacyRawBaselineBootstrapper(store).run(
        account_id=ACCOUNT_ID,
        snapshot_run_id=RUN_ID,
        email_address="user@example.com",
        batch_size=1,
        progress=lambda processed, total: progress.append((processed, total)),
    )

    baseline = NormalizedBaselineReader(store).read(RUN_ID, ACCOUNT_ID)

    assert result.history_id == "100"
    assert result.messages_processed == 2
    assert result.reused is False
    assert result.row_counts["accounts"] == 1
    assert result.row_counts["messages"] == 2
    assert {row["message_id"] for row in baseline.tables["messages"]} == {"m1", "m2"}
    assert baseline.account.email_address == "user@example.com"
    assert baseline.account.history_id == "100"
    assert progress == [(1, 2), (2, 2)]


def test_bootstrap_is_idempotent_when_complete_manifest_exists(tmp_path: Path) -> None:
    store = _seed_snapshot(tmp_path / "artifacts")
    service = LegacyRawBaselineBootstrapper(store)

    first = service.run(
        account_id=ACCOUNT_ID,
        snapshot_run_id=RUN_ID,
        email_address="user@example.com",
        batch_size=2,
    )
    second = service.run(
        account_id=ACCOUNT_ID,
        snapshot_run_id=RUN_ID,
        email_address="user@example.com",
        batch_size=2,
    )

    assert first.reused is False
    assert second.reused is True
    assert second.row_counts == first.row_counts
    assert second.messages_processed == 2


def test_bootstrap_rejects_incomplete_snapshot(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    queue = MessageDiscoveryQueue(store, RUN_ID, ACCOUNT_ID, "100")
    queue.merge_page(
        [{"id": "m1", "threadId": "t1"}],
        page_index=0,
        discovered_at=T0,
    )
    queue.persist()

    with pytest.raises(LegacyBaselineBootstrapError, match="discovery is not complete"):
        LegacyRawBaselineBootstrapper(store).run(
            account_id=ACCOUNT_ID,
            snapshot_run_id=RUN_ID,
            email_address="user@example.com",
        )


def test_bootstrap_rejects_fetched_record_without_raw(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path / "artifacts")
    queue = MessageDiscoveryQueue(store, RUN_ID, ACCOUNT_ID, "100")
    queue.merge_page(
        [{"id": "m1", "threadId": "t1"}],
        page_index=0,
        discovered_at=T0,
    )
    queue.mark_discovery_complete()
    queue.mark_fetched("m1", attempts=1)
    queue.persist()

    with pytest.raises(LegacyBaselineBootstrapError):
        LegacyRawBaselineBootstrapper(store).run(
            account_id=ACCOUNT_ID,
            snapshot_run_id=RUN_ID,
            email_address="user@example.com",
        )
