# Incremental Mutations Runbook — 0.4.0b2

## Purpose

Operate and inspect Gmail History additions, deletions and label mutations.

## Run

```bash
gmail-ingest incremental \
  --checkpoint-root .gmail-ingestion-control \
  --artifact-root .gmail-ingestion
```

## Supported events

```text
MESSAGE_ADDED
MESSAGE_DELETED
LABEL_ADDED
LABEL_REMOVED
```

## Expected artifact layout

```text
runs/gmail/history/<run_id>/
├── raw/
│   └── history-page-000000.json
├── history_events/
│   └── <event_id>.json
├── message_tombstones/
│   └── <source_event_id>.json
├── message_label_mutations/
│   └── <source_event_id>.json
└── incremental_manifest.json
```

Changed message bodies remain in the existing `gmail.messages` RAW namespace.

## Event lifecycle

Before applying an effect:

```json
{
  "processing_status": "PENDING",
  "processed_at": null
}
```

After the effect is durably persisted:

```json
{
  "processing_status": "PROCESSED",
  "processed_at": "<UTC>"
}
```

A PENDING event in a failed run is diagnostic evidence that the reducer did not complete.

## Deletion

A Gmail `messagesDeleted[]` event means the message was deleted from the mailbox, not merely moved
to Trash.

The connector emits a tombstone rather than attempting `messages.get` for the deleted resource.

## Label addition

```text
LABEL_ADDED
→ UPSERT_ACTIVE
→ is_active = true
```

## Label removal

```text
LABEL_REMOVED
→ DEACTIVATE
→ is_active = false
```

One Gmail label-change entry containing several `labelIds` produces one elementary event and one
mutation per label ID.

## Duplicate event in the same run

The deterministic `event_id` is inserted into an in-memory seen set.

```text
same event_id again
→ increment duplicate_events_ignored
→ do not reduce twice
```

## Failure rule

Do not manually advance the checkpoint when a reducer effect fails.

Expected state:

```text
old checkpoint remains COMMITTED
failed event may remain PENDING
candidate is absent
```

## Current reliability boundary

The beta does not yet promise restart of the exact same partially written run because immutable RAW
History page files intentionally reject overwrite.

That scenario, plus crash-after-effect, cross-run replay and automatic Full Snapshot fallback, is
the scope of `0.4.0rc1`.
