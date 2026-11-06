# Legacy RAW Baseline Bootstrap — 0.5.0rc4

## Purpose

Reuse a completed Gmail `0.4.x` Full Snapshot as the normalized baseline required by the
`0.5.x` projection engine **without re-downloading mailbox messages**.

The 0.4 reliability flow persisted:

```text
gmail.messages/<snapshot_run_id>/
├── control/message_discovery.json
└── raw/message-<id>-full.json
```

but did not automatically materialize `gmail.normalized` Parquet during Full Snapshot recovery.

`0.5.0rc4` adds a local compatibility path:

```text
legacy 0.4 RAW Full Snapshot
        ↓
streaming local normalization
        ↓
gmail.projection_baseline/<snapshot_run_id>/
        ├── normalized/accounts.parquet
        ├── normalized/messages.parquet
        ├── normalized/message_headers.parquet
        ├── normalized/message_recipients.parquet
        ├── normalized/message_labels.parquet
        ├── normalized/mime_parts.parquet
        ├── normalized/attachments.parquet
        └── baseline_manifest.json
        ↓
gmail-ingest project
```

No `users.messages.get` request is performed by this bootstrap.

## Preconditions

The source snapshot must satisfy:

```text
message discovery complete
history_start present
failed_count = 0
every discovery record = FETCHED
every FETCHED record has immutable RAW JSON
```

The bootstrap fails closed if any condition is missing.

## Command

Example:

```bash
gmail-ingest bootstrap-baseline \
  --account-id acct_... \
  --snapshot-run-id <FULL_SNAPSHOT_RUN_ID> \
  --email-address user@example.com \
  --artifact-root .gmail-ingestion-live-0.5.0
```

The default streaming batch is:

```text
250 RAW messages
```

Progress is emitted every 5,000 messages by default.

For a large mailbox:

```bash
gmail-ingest bootstrap-baseline \
  --account-id acct_... \
  --snapshot-run-id <FULL_SNAPSHOT_RUN_ID> \
  --email-address user@example.com \
  --artifact-root .gmail-ingestion-live-0.5.0 \
  --batch-size 250 \
  --progress-every 5000
```

## Memory model

The command does not load the entire mailbox into memory.

```text
discovery metadata
    ↓
sorted message IDs
    ↓
batch of RAW messages
    ↓
normalize batch
    ↓
append Parquet row groups
    ↓
release batch memory
```

This is specifically intended for full-mailbox snapshots whose RAW footprint may be tens of
gigabytes.

## Idempotence

`baseline_manifest.json` is published last.

If it exists with all required Parquet files, rerunning the command returns:

```text
reused: true
```

and does not re-normalize the mailbox.

A completion manifest with missing Parquet evidence is rejected.

## Projection reader compatibility

`NormalizedBaselineReader` uses:

1. standard `gmail.normalized/.../candidate/normalized` when present;
2. otherwise the RC4 `gmail.projection_baseline/.../normalized` compatibility baseline.

The public `gmail-ingest project` command does not need a different baseline run ID.

## Scope

The compatibility baseline persists only the tables required to reconstruct Current State:

```text
accounts
messages
message_headers
message_recipients
message_labels
mime_parts
attachments
```

`threads` are deliberately recomputed by the projection engine from current messages and
recipients.

The global `labels` catalog is not required for projection reduction; current message-label
relations retain Gmail label IDs directly.

## Safety

The legacy RAW snapshot remains immutable.

The bootstrap only writes a derived namespace:

```text
gmail.projection_baseline
```

It does not modify:

```text
gmail.messages
gmail.history
checkpoint state
receipt state
projection current pointer
```


## Resumable RC6 segmented layout

Starting with 0.5.0rc6, the bootstrap writer persists six Parquet tables for each
bounded batch, and atomically publishes the segment commit marker LAST:

```text
gmail.projection_baseline/<snapshot-run-id>/
  segments/
    00000000/
      messages.parquet
      message_headers.parquet
      message_recipients.parquet
      message_labels.parquet
      mime_parts.parquet
      attachments.parquet
      segment.json
    00000001/
      ...
  normalized/
    accounts.parquet
  baseline_manifest.json           # COMPLETED only after every segment
```

On restart, every committed segment is checked against its SHA-256 evidence and
source identity. The same snapshot run ID and batch size must be reused; a
different batch size is rejected to prevent ambiguous segmentation.

The operator can rerun the same `gmail-ingest bootstrap-baseline` command after an
interruption. Already committed batches are reused; only missing segments are
normalized. There is no Gmail API call and no rewrite of the legacy RAW source.

`baseline_manifest.json` remains the only publication signal for a complete
baseline. An incomplete set of segments must never be treated as a valid
projection baseline.

### Space and memory

The segment writer avoids making a second full physical Parquet copy to finalize,
but new normalized data still requires disk space. Check `df -h .` before running,
especially when the volume is almost full.

The projection reader can load segment files individually, but the current projection
reducer still materializes normalized rows in RAM. RC6 hardens bootstrap persistence,
not the memory model of the entire Current State projector.
