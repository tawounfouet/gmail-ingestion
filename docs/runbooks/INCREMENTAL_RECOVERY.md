# Incremental Recovery Runbook — 0.4.0rc1

## Purpose

Operate, diagnose and safely resume the Gmail incremental pipeline after interruptions or an
expired Gmail History cursor.

## Normal operation

```bash
gmail-ingest incremental \
  --checkpoint-root .gmail-ingestion-control \
  --artifact-root .gmail-ingestion
```

Automatic full-snapshot recovery is enabled by default.

Strict incremental-only mode:

```bash
gmail-ingest incremental --no-auto-full-resync
```

## Control-plane state

Checkpoint files:

```text
accounts/<account_id>/checkpoints/
├── committed.json
├── invalid.json
└── candidates/<run_id>.json
```

Durable event receipts:

```text
accounts/<account_id>/history_receipts/<prefix>/<event_id>.json
```

## Run states

The incremental manifest may contain:

```text
RUNNING
READY_TO_COMMIT
COMPLETED
RECOVERED_COMMIT
FAILED
FULL_RESYNC_REQUIRED
```

### RUNNING

History/event work is in progress. Re-run with the **same run ID** to reuse frozen pages.

### READY_TO_COMMIT

All History pages and reducer effects succeeded and a candidate exists. Re-running the same run ID
promotes the candidate if the predecessor checkpoint still matches.

### RECOVERED_COMMIT

The transaction had already committed, or its persisted candidate was safely promoted during
recovery.

### FULL_RESYNC_REQUIRED

Gmail rejected the committed `startHistoryId`. The strict incremental layer invalidated that
cursor. The reliable wrapper may now start a qualified Full Snapshot.

## Crash during History pagination

Do not delete the run directory.

Restart using the same run UUID:

```bash
gmail-ingest incremental --run-id <uuid>
```

Already frozen pages are read locally; Gmail is contacted only for pages not yet frozen.

## Crash after event effect

Possible state:

```text
history_event = PENDING
RAW message / tombstone / label mutation already exists
checkpoint = old COMMITTED
```

Restart the same run. Existing run artifacts are reused and the event is completed without
premature checkpoint advancement.

## Crash after receipt but before PROCESSED

The durable receipt proves the logical event was applied. Replay marks the event PROCESSED without
repeating its business effect.

## Crash after candidate persistence

Possible state:

```text
manifest = READY_TO_COMMIT
candidate = present
old checkpoint = COMMITTED
```

Restart the same run. The candidate is promoted only if the old committed history ID still matches
the expected predecessor.

## Crash after commit

If the committed checkpoint carries the same run ID, the run returns `RECOVERED_COMMIT` without
re-contacting Gmail.

## Concurrent/stale run

A stale run may fail with `CheckpointConflictError`.

Do not manually overwrite `committed.json`. Inspect the current committed checkpoint and start a
new run from that authoritative cursor.

## Expired Gmail History cursor

With automatic recovery enabled:

```text
HTTP 404 / expired history
→ CAS invalidate old cursor
→ Full Snapshot
→ require COMPLETED
→ bootstrap snapshot.history_start
→ History catch-up
→ final COMMITTED
```

If the Full Snapshot is `PARTIAL` or `FAILED`, no bootstrap checkpoint is committed.

## Receipt schema

```text
history_receipt_schema_version = 1
```

Receipts are account scoped and keyed by deterministic `event_id`. They are control-plane
evidence and should be backed up with the checkpoint directory.

## Stable release gate

Before promoting `0.4.0rc1` to stable `0.4.0`, run a bounded real-mailbox qualification that
covers at minimum:

```text
initial Full Snapshot
→ committed bootstrap
→ create/modify/delete a bounded set of test messages/labels
→ incremental catch-up
→ restart/replay check
→ compare final Gmail state with persisted evidence
```

Do not report the live gate as passed until it has actually been executed.
