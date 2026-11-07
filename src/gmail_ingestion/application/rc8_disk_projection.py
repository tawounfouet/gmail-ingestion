"""Disk-backed, bounded-memory RC8 Current reducer and Parquet writer.

The SQLite staging database is private, disposable only by an explicit operator,
and never part of the authoritative Current control plane. All content remains
local. Neither this module nor its callers change a Gmail mailbox.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from collections.abc import Iterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pyarrow as pa
import pyarrow.parquet as pq

from gmail_ingestion.domain import GmailHistoryEvent, GmailHistoryEventType
from gmail_ingestion.persistence.projection_dataset_store import (
    NormalizedBaselineReader,
    _arrow_type,
)
from gmail_ingestion.projection.message_delta import (
    CURRENT_PROJECTION_TABLES,
    MESSAGE_PROJECTION_TABLES,
    PreparedMessageDelta,
)
from gmail_ingestion.schemas import TABLE_CONTRACTS, TABLE_SORT_KEYS


class RC8DiskProjectionError(RuntimeError):
    """Invalid source provenance, unsafe output, or inconsistent projection."""


_NON_LOGICAL_FIELDS = frozenset({
    "dataset_version", "source_connector_version", "ingested_at",
    "updated_at", "profile_fetched_at", "downloaded_at",
})


def _sortable(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _encoded(row: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(row),
        default=lambda obj: obj.isoformat() if isinstance(obj, datetime) else _bad_type(),
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    )


def _bad_type() -> None:
    raise TypeError("Unsupported normalized row field type")


def _decoded(name: str, raw: str) -> dict[str, Any]:
    row: dict[str, Any] = json.loads(raw)
    for field in TABLE_CONTRACTS[name].fields:
        value = row.get(field.name)
        if field.type.startswith("timestamp[") and isinstance(value, str):
            row[field.name] = datetime.fromisoformat(value)
    return row


def _pk(name: str, row: Mapping[str, Any]) -> str:
    return json.dumps(
        [_sortable(row.get(field)) for field in TABLE_CONTRACTS[name].primary_key],
        separators=(",", ":"), ensure_ascii=False,
    )


def _sort_tuple(name: str, row: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(_sortable(row.get(k)) for k in TABLE_SORT_KEYS[name])


class DiskCurrentProjection:
    """SQLite indexed message bundles with bounded scan and persistent counts."""

    def __init__(self, path: Path, account_id: str) -> None:
        if path.exists():
            raise RC8DiskProjectionError("Immutable scratch database already exists")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.account_id = account_id
        self.path = path
        self.conn = sqlite3.connect(str(path))
        os.chmod(path, 0o600)
        self.conn.execute("PRAGMA synchronous=FULL")
        self.conn.execute("PRAGMA temp_store=FILE")
        self.conn.execute("PRAGMA cache_size=-32768")
        self.conn.execute("""
            CREATE TABLE records (
                table_name TEXT NOT NULL,
                message_id TEXT NOT NULL,
                row_pk TEXT NOT NULL,
                sort0 TEXT NOT NULL,
                sort1 TEXT NOT NULL,
                sort2 TEXT NOT NULL,
                sort3 TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (table_name, row_pk)
            ) WITHOUT ROWID
        """)
        self.conn.execute(
            "CREATE INDEX by_message ON records(table_name, message_id)"
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def _insert(self, name: str, row: Mapping[str, Any]) -> None:
        if row.get("account_id") != self.account_id:
            raise RC8DiskProjectionError("Cross-account normalized row")
        if name not in CURRENT_PROJECTION_TABLES:
            raise RC8DiskProjectionError("Unknown Current table")
        message_id = row.get("message_id")
        if name == "threads":
            message_id = "thread:" + str(row.get("thread_id"))
        if not isinstance(message_id, str) or not message_id:
            raise RC8DiskProjectionError("Invalid normalized message key")
        order = (*_sort_tuple(name, row), "", "", "", "")[:4]
        try:
            self.conn.execute(
                "INSERT INTO records VALUES (?,?,?,?,?,?,?,?)",
                (name, message_id, _pk(name, row), *order, _encoded(row)),
            )
        except sqlite3.IntegrityError as exc:
            raise RC8DiskProjectionError("Duplicate normalized primary key") from exc

    def _remove_message(self, message_id: str) -> None:
        for name in MESSAGE_PROJECTION_TABLES:
            self.conn.execute(
                "DELETE FROM records WHERE table_name=? AND message_id=?",
                (name, message_id),
            )

    def _contains_message(self, message_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM records WHERE table_name='messages' AND message_id=?",
            (message_id,),
        ).fetchone() is not None

    def replace_bundle(
        self, message_id: str,
        bundle: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> None:
        messages = bundle.get("messages", ())
        if len(messages) != 1 or messages[0].get("message_id") != message_id:
            raise RC8DiskProjectionError("Message recovery bundle is incomplete")
        for name in MESSAGE_PROJECTION_TABLES:
            for row in bundle.get(name, ()):
                if row.get("message_id") != message_id:
                    raise RC8DiskProjectionError("Cross-message normalized bundle")
        with self.conn:
            self._remove_message(message_id)
            for name in MESSAGE_PROJECTION_TABLES:
                for row in bundle.get(name, ()):
                    self._insert(name, row)

    def ingest_baseline(
        self, reader: NormalizedBaselineReader, run_id: UUID, *,
        max_rows_per_batch: int = 2048,
    ) -> dict[str, int]:
        """Consume every verified baseline segment, never its eager read() API."""
        counts: dict[str, int] = {}
        for name in MESSAGE_PROJECTION_TABLES:
            observed = 0
            for batch in reader.iter_verified_segmented_table(
                run_id, name, max_rows_per_batch=max_rows_per_batch
            ):
                with self.conn:
                    for row in batch:
                        self._insert(name, row)
                observed += len(batch)
            counts[name] = observed
        return counts

    def apply_deltas(
        self, deltas: Sequence[PreparedMessageDelta],
    ) -> dict[str, int]:
        seen: set[str] = set()
        duplicates = 0
        for delta in sorted(
            deltas,
            key=lambda d: (
                int(d.event.history_id), d.event.event_ordinal, d.event.event_id
            ),
        ):
            event = delta.event
            if event.account_id != self.account_id:
                raise RC8DiskProjectionError("Cross-account History event")
            if event.event_id in seen:
                duplicates += 1
                continue
            seen.add(event.event_id)
            if event.event_type is GmailHistoryEventType.MESSAGE_ADDED:
                if delta.normalized_rows is None:
                    raise RC8DiskProjectionError("Missing normalized added-message bundle")
                self.replace_bundle(event.message_id, delta.normalized_rows)
            elif event.event_type is GmailHistoryEventType.MESSAGE_DELETED:
                if delta.normalized_rows is not None:
                    raise RC8DiskProjectionError("Delete event carries unexpected rows")
                with self.conn:
                    self._remove_message(event.message_id)
            elif event.event_type in {
                GmailHistoryEventType.LABEL_ADDED, GmailHistoryEventType.LABEL_REMOVED
            }:
                self._apply_label(event)
            else:
                raise RC8DiskProjectionError("Unsupported History event class")
        return {"duplicate_events_ignored": duplicates}

    def _apply_label(self, event: GmailHistoryEvent) -> None:
        if not isinstance(event.label_id, str) or not event.label_id:
            raise RC8DiskProjectionError("Missing label relation identifier")
        if not self._contains_message(event.message_id):
            return
        pk = _pk("message_labels", {
            "account_id": self.account_id,
            "message_id": event.message_id,
            "label_id": event.label_id,
        })
        result = self.conn.execute(
            "SELECT payload FROM records WHERE table_name='message_labels' AND row_pk=?",
            (pk,),
        ).fetchone()
        with self.conn:
            self.conn.execute(
                "DELETE FROM records WHERE table_name='message_labels' AND row_pk=?",
                (pk,),
            )
            if event.event_type is GmailHistoryEventType.LABEL_ADDED:
                prior = _decoded("message_labels", result[0]) if result else None
                self._insert("message_labels", {
                    "account_id": self.account_id,
                    "message_id": event.message_id,
                    "label_id": event.label_id,
                    "added_history_id": event.history_id,
                    "removed_history_id": None,
                    "is_active": True,
                    "first_seen_at": (
                        prior.get("first_seen_at") if prior is not None
                        else event.observed_at
                    ),
                    "last_seen_at": event.observed_at,
                })

    def reconcile_scope(
        self, source_ids: set[str],
        recovery_rows: Mapping[str, Mapping[str, Sequence[Mapping[str, Any]]]],
    ) -> dict[str, int]:
        projected = {
            row[0] for row in self.conn.execute(
                "SELECT message_id FROM records WHERE table_name='messages'"
            )
        }
        missing = source_ids - projected
        extra = projected - source_ids
        if not missing.issubset(recovery_rows):
            raise RC8DiskProjectionError("Source-only IDs lack verified current RAW")
        for mid in sorted(missing):
            self.replace_bundle(mid, recovery_rows[mid])
        with self.conn:
            for mid in sorted(extra):
                self._remove_message(mid)
        return {
            "source_message_count": len(source_ids),
            "source_only_recovered_count": len(missing),
            "outside_source_pruned_count": len(extra),
        }

    def iter_rows(self, name: str, *, batch_size: int = 2048) -> Iterator[list[dict[str, Any]]]:
        if name not in CURRENT_PROJECTION_TABLES:
            raise RC8DiskProjectionError("Unknown normalized table")
        cursor = self.conn.execute(
            """SELECT payload FROM records WHERE table_name=?
               ORDER BY sort0, sort1, sort2, sort3""",
            (name,),
        )
        while chunk := cursor.fetchmany(batch_size):
            yield [_decoded(name, raw) for (raw,) in chunk]

    def row_counts(self) -> dict[str, int]:
        return {
            name: int(self.conn.execute(
                "SELECT COUNT(*) FROM records WHERE table_name=?", (name,)
            ).fetchone()[0])
            for name in CURRENT_PROJECTION_TABLES
        }

    def finalize(self, source_ids: set[str], events: Sequence[GmailHistoryEvent]) -> None:
        """Validate scoped foreign keys/MIME, then rebuild thread relations."""
        actual = {
            row[0] for row in self.conn.execute(
                "SELECT message_id FROM records WHERE table_name='messages'"
            )
        }
        if actual != source_ids or self.row_counts()["messages"] != len(source_ids):
            raise RC8DiskProjectionError("Current message ID set differs from scoped source")

        for name in MESSAGE_PROJECTION_TABLES[1:]:
            dangling = self.conn.execute(
                """SELECT 1 FROM records child
                   LEFT JOIN records parent
                     ON parent.table_name='messages'
                    AND parent.message_id=child.message_id
                   WHERE child.table_name=? AND parent.message_id IS NULL LIMIT 1""",
                (name,),
            ).fetchone()
            if dangling is not None:
                raise RC8DiskProjectionError("Current message child reference is dangling")

        for chunk in self.iter_rows("message_labels"):
            for relation in chunk:
                if relation.get("is_active") is True and relation.get("label_id") in {
                    "SPAM", "TRASH"
                }:
                    raise RC8DiskProjectionError("Current includes excluded Spam/Trash label")

        for name in ("mime_parts", "attachments"):
            for chunk in self.iter_rows(name):
                for row in chunk:
                    parent_path = (
                        row.get("parent_part_path") if name == "mime_parts"
                        else row.get("part_path")
                    )
                    if parent_path is None:
                        continue
                    key = _pk("mime_parts", {
                        "account_id": self.account_id,
                        "message_id": row["message_id"],
                        "part_path": parent_path,
                    })
                    if not self.conn.execute(
                        "SELECT 1 FROM records WHERE table_name='mime_parts' AND row_pk=?",
                        (key,),
                    ).fetchone():
                        raise RC8DiskProjectionError("MIME or attachment reference is dangling")
        self._recompute_threads(events)

    def _recompute_threads(self, events: Sequence[GmailHistoryEvent]) -> None:
        """Disk-backed aggregation matching recompute_threads ordering."""
        self.conn.execute(
            """CREATE TABLE thread_state(
                thread_id TEXT PRIMARY KEY, payload TEXT NOT NULL
            )"""
        )
        self.conn.execute(
            """CREATE TABLE message_threads(
                message_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL
            )"""
        )
        self.conn.execute(
            """CREATE TABLE thread_participants(
                thread_id TEXT NOT NULL, email TEXT NOT NULL,
                PRIMARY KEY(thread_id, email)
            ) WITHOUT ROWID"""
        )
        latest_event_time = max(
            (e.observed_at for e in events), default=datetime(1970, 1, 1, tzinfo=UTC)
        )
        event_max_history: dict[str, int] = {}
        for event in events:
            if event.account_id != self.account_id:
                raise RC8DiskProjectionError("Cross-account event in thread calculation")
            if event.thread_id:
                event_max_history[event.thread_id] = max(
                    event_max_history.get(event.thread_id, 0),
                    int(event.history_id),
                )

        for chunk in self.iter_rows("messages"):
            with self.conn:
                for msg in chunk:
                    tid = msg.get("thread_id")
                    mid = msg["message_id"]
                    if not isinstance(tid, str) or not tid:
                        raise RC8DiskProjectionError("Message has no valid thread ID")
                    self.conn.execute(
                        "INSERT INTO message_threads VALUES (?,?)", (mid, tid)
                    )
                    for time_key in ("updated_at", "ingested_at"):
                        candidate = msg.get(time_key)
                        if isinstance(candidate, datetime):
                            latest_event_time = max(latest_event_time, candidate)
                    when = msg.get("internal_date")
                    order = (when.isoformat() if isinstance(when, datetime) else "", mid)
                    existing = self.conn.execute(
                        "SELECT payload FROM thread_state WHERE thread_id=?", (tid,)
                    ).fetchone()
                    if existing is None:
                        info: dict[str, Any] = {
                            "count": 1,
                            "first_key": order, "last_key": order,
                            "first_at": when.isoformat() if isinstance(when, datetime) else None,
                            "last_at": when.isoformat() if isinstance(when, datetime) else None,
                            "first_mid": mid, "last_mid": mid,
                            "snippet": msg.get("snippet"),
                            "has_attachments": bool(msg.get("has_attachments")),
                            "history": int(msg["history_id"]) if msg.get("history_id") else 0,
                        }
                    else:
                        info = json.loads(existing[0])
                        info["count"] += 1
                        if order < tuple(info["first_key"]):
                            info["first_key"] = order
                            info["first_at"] = (
                                when.isoformat() if isinstance(when, datetime) else None
                            )
                            info["first_mid"] = mid
                        if order > tuple(info["last_key"]):
                            info["last_key"] = order
                            info["last_at"] = (
                                when.isoformat() if isinstance(when, datetime) else None
                            )
                            info["last_mid"] = mid
                            info["snippet"] = msg.get("snippet")
                        info["has_attachments"] = (
                            info["has_attachments"] or bool(msg.get("has_attachments"))
                        )
                        if msg.get("history_id"):
                            info["history"] = max(info["history"], int(msg["history_id"]))
                    self.conn.execute(
                        """INSERT INTO thread_state VALUES (?,?)
                           ON CONFLICT(thread_id) DO UPDATE SET payload=excluded.payload""",
                        (tid, json.dumps(info, separators=(",", ":"), ensure_ascii=False)),
                    )

        for chunk in self.iter_rows("message_recipients"):
            with self.conn:
                for recipient in chunk:
                    email = recipient.get("email_address")
                    if not isinstance(email, str) or not email:
                        continue
                    link = self.conn.execute(
                        "SELECT thread_id FROM message_threads WHERE message_id=?",
                        (recipient["message_id"],),
                    ).fetchone()
                    if link is None:
                        raise RC8DiskProjectionError("Recipient points to missing message")
                    self.conn.execute(
                        "INSERT OR IGNORE INTO thread_participants VALUES (?,?)",
                        (link[0], email.lower()),
                    )

        cursor = self.conn.execute(
            "SELECT thread_id,payload FROM thread_state ORDER BY thread_id"
        )
        while batch := cursor.fetchmany(2048):
            with self.conn:
                for tid, data in batch:
                    info = json.loads(data)
                    participant_count = self.conn.execute(
                        "SELECT COUNT(*) FROM thread_participants WHERE thread_id=?",
                        (tid,),
                    ).fetchone()[0]
                    history = max(int(info["history"]), event_max_history.get(tid, 0))
                    self._insert("threads", {
                        "account_id": self.account_id,
                        "thread_id": tid,
                        "history_id": str(history) if history else None,
                        "snippet": info["snippet"],
                        "message_count": int(info["count"]),
                        "first_message_at": (
                            datetime.fromisoformat(info["first_at"])
                            if info["first_at"] is not None else None
                        ),
                        "last_message_at": (
                            datetime.fromisoformat(info["last_at"])
                            if info["last_at"] is not None else None
                        ),
                        "first_message_id": info["first_mid"],
                        "last_message_id": info["last_mid"],
                        "participant_count": int(participant_count),
                        "has_attachments": bool(info["has_attachments"]),
                        "is_deleted": False,
                        "ingested_at": latest_event_time,
                        "updated_at": latest_event_time,
                    })

    def logical_digest(self) -> str:
        """Match the existing canonical logical digest without full table lists."""
        digest = hashlib.sha256()
        digest.update(b"{")
        for index, name in enumerate(sorted(CURRENT_PROJECTION_TABLES)):
            if index:
                digest.update(b",")
            digest.update(json.dumps(name, ensure_ascii=False).encode("utf-8"))
            digest.update(b":[")
            first = True
            for chunk in self.iter_rows(name):
                for row in chunk:
                    if not first:
                        digest.update(b",")
                    first = False
                    canonical = {
                        k: v.isoformat() if isinstance(v, datetime) else v
                        for k, v in row.items() if k not in _NON_LOGICAL_FIELDS
                    }
                    digest.update(json.dumps(
                        canonical, ensure_ascii=False, sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8"))
            digest.update(b"]")
        digest.update(b"}")
        return digest.hexdigest()

    def write_parquet(self, directory: Path, *, batch_size: int = 2048) -> dict[str, str]:
        """Atomic per-table Parquet from bounded row groups; no complete manifest."""
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        digests: dict[str, str] = {}
        for name in CURRENT_PROJECTION_TABLES:
            schema = pa.schema([
                pa.field(field.name, _arrow_type(pa, field.type), nullable=field.nullable)
                for field in TABLE_CONTRACTS[name].fields
            ])
            dest = directory / f"{name}.parquet"
            tmp = directory / f".{name}.{uuid.uuid4().hex}.tmp"
            try:
                with pq.ParquetWriter(str(tmp), schema) as writer:
                    count = 0
                    for chunk in self.iter_rows(name, batch_size=batch_size):
                        writer.write_table(pa.Table.from_pylist(chunk, schema=schema))
                        count += len(chunk)
                    if count != self.row_counts()[name]:
                        raise RC8DiskProjectionError("Parquet export row count mismatch")
                os.replace(tmp, dest)
                os.chmod(dest, 0o600)
                digest = hashlib.sha256()
                with dest.open("rb") as stream:
                    for block in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(block)
                digests[name] = digest.hexdigest()
            finally:
                # Incomplete candidate data is preserved, not automatically overwritten.
                # Temporary files are retained on failure for forensic diagnostics.
                pass
        return digests
