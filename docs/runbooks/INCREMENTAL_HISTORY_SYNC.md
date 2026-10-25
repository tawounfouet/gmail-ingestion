# Incremental History Sync Runbook — 0.4.0b1

## Purpose

Run the Gmail History incremental path after a durable committed checkpoint already exists.

## Preconditions

```text
OAuth account configured
gmail.readonly scope
committed checkpoint present
artifact storage writable
checkpoint storage writable
```

The beta does not automatically seed a checkpoint from the current Gmail profile.

## Run

```bash
gmail-ingest incremental \
  --checkpoint-root .gmail-ingestion-control \
  --artifact-root .gmail-ingestion
```

Optional:

```bash
gmail-ingest incremental --max-results 100
```

## Expected transaction

```text
load committed H100
   ↓
history.list(H100)
   ↓
page 0
   ↓
page 1 ...
   ↓
persist history pages
   ↓
persist MESSAGE_ADDED events
   ↓
hydrate unique message IDs with format=full
   ↓
persist immutable RAW messages
   ↓
candidate H125
   ↓
commit H125
```

## Evidence

History run artifacts are stored under the `gmail.history` job namespace.

```text
raw/history-page-000000.json
raw/history-page-000001.json
history_events/<event_id>.json
incremental_manifest.json
```

Hydrated messages remain under the existing `gmail.messages` job namespace.

## Successful manifest

Important fields:

```text
mode                  = incremental_history
history_types         = [messageAdded]
input_checkpoint      = H100
candidate_checkpoint  = H125
committed_checkpoint  = H125
message_format        = full
status                = COMPLETED
```

## Failure before commit

If an API, history persistence, event persistence or message hydration operation fails:

```text
status = FAILED
previous committed checkpoint remains authoritative
```

Rerunning from the committed checkpoint may replay already observed events. Event IDs are
deterministic so downstream reducers can remain idempotent.

## Expired or invalid history ID

If Gmail rejects `startHistoryId` with HTTP 404:

```text
committed checkpoint
   ↓
INVALID
   ↓
FULL_RESYNC_REQUIRED
```

The command exits with an error indicating that a Full Sync is required.

Do not fabricate an intermediate history ID and do not increment the old history ID numerically.

## Current beta event scope

```text
messageAdded     processed
messageDeleted   not yet processed
labelAdded       not yet processed
labelRemoved     not yet processed
```

The three deferred mutation types enter in `0.4.0b2`.
