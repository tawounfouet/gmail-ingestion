# Gmail Ingestion 0.4.0 — Migration from 0.3.0

## Summary

No normalized dataset migration is required.

```text
0.3.0 normalized_schema_version = 1
0.4.0 normalized_schema_version = 1
```

Existing snapshot and filtered-ingestion commands remain available.

## New incremental command

```bash
gmail-ingest incremental \
  --checkpoint-root .gmail-ingestion-control \
  --artifact-root .gmail-ingestion
```

By default, if no usable committed checkpoint exists, the reliable incremental workflow can perform
a complete Full Snapshot, seed a checkpoint from the snapshot history baseline and catch up through
Gmail History.

To require incremental-only behavior:

```bash
gmail-ingest incremental --no-auto-full-resync
```

## New persistent control state

0.4.0 introduces per-account control artifacts:

```text
accounts/<account_id>/
├── checkpoints/
│   ├── committed.json
│   ├── invalid.json
│   └── candidates/
└── history_receipts/
```

These should be treated as durable operational state when using incremental synchronization.

## New mutation artifacts

Incremental runs may produce:

```text
gmail.history/<run_id>/
├── raw/
├── history_events/
├── message_tombstones/
├── message_label_mutations/
└── incremental_manifest.json
```

These do not alter normalized schema v1. Consumers that want a current-state projection must apply
the incremental mutation semantics on top of their chosen snapshot/baseline model.

## Operational recommendation

Before enabling 0.4.0 incremental synchronization in production:

1. choose durable artifact and checkpoint roots;
2. retain the checkpoint root across process restarts;
3. do not share a local checkpoint root between uncoordinated concurrent writers;
4. allow automatic Full Snapshot recovery only where its storage footprint is acceptable;
5. monitor free disk space for large mailboxes.

## Rollback

Rolling back to 0.3.0 does not require converting normalized datasets, but 0.3.0 does not understand
or advance 0.4.0 History checkpoints. Preserve the 0.4.0 control root if a later return to
incremental synchronization is expected.
