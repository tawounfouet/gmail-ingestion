# Gmail Ingestion

Reliable, versioned Gmail ingestion built on [PyIngestKit](https://github.com/tawounfouet/pyingestkit).

> **Stable release:** `v0.4.0 — Incremental Sync Stable`

`0.4.0` adds Gmail History incremental synchronization, committed checkpoints, deletion and label
mutation handling, durable replay receipts, crash-safe recovery and automatic Full Snapshot bootstrap
while preserving normalized schema v1 and the read-only Gmail authorization contract.

```text
Gmail API
   ↓
Immutable RAW
   ↓
Deterministic normalization
   ↓
Typed relational tables
   ↓
Quality checks
   ↓
Parquet
```

## Stable foundation

The published `0.2.0` release was qualified on:

```text
Python 3.11       PASS
Python 3.12       PASS
Python 3.13       PASS
Ruff              PASS
mypy strict       PASS
wheel + sdist     PASS
twine check       PASS
clean install     PASS
PyIngestKit       v2.1.0 verified
```

The source-extraction layer remains based on the qualified `gmail.readonly` Snapshot Foundation.
The `0.3.0` release changes message discovery scope only; RAW hydration and normalization remain unchanged.

## Stable normalized contract

```text
normalized_schema_version = 1
```

Tables:

```text
accounts
labels
messages
threads
message_headers
message_recipients
message_labels
mime_parts
attachments
```

Multi-valued Gmail data stays relational rather than being flattened into convenience arrays.
Threads are materialized aggregates that remain derivable from messages.

## Requirements

- Python 3.11, 3.12 or 3.13;
- Git;
- Google Cloud project with Gmail API enabled for source ingestion;
- OAuth 2.0 Desktop client JSON as `credentials.json`.

PyIngestKit is pinned to:

```text
v2.1.0
```

## Install

```bash
git clone https://github.com/tawounfouet/gmail-ingestion.git
cd gmail-ingestion
git checkout v0.4.0
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
gmail-ingest version
```

Expected:

```text
0.4.0
```

## Source snapshot

```bash
gmail-ingest auth
gmail-ingest profile
gmail-ingest labels
gmail-ingest snapshot
```

Optional attachment binaries:

```bash
gmail-ingest snapshot \
  --download-attachments \
  --max-attachment-bytes 10485760
```

## Filtered snapshot — 0.3.0

Gmail-native query:

```bash
gmail-ingest snapshot --query "has:attachment"
```

Label-filtered discovery:

```bash
gmail-ingest snapshot --label-id IMPORTANT --label-id INBOX
```

Combined search, labels and Spam/Trash policy:

```bash
gmail-ingest snapshot \
  --query "from:example.com" \
  --label-id IMPORTANT \
  --include-spam-trash
```

The filter is captured in the run manifest with requested/effective values and a deterministic
scope fingerprint.

Date range:

```bash
gmail-ingest range \
  --after 2026-10-01 \
  --before 2026-10-24
```

Relative range frozen at run start:

```bash
gmail-ingest range --last-days 7
```

Combined source scope:

```bash
gmail-ingest range \
  --query "has:attachment" \
  --label-id IMPORTANT \
  --after 2026-10-01 \
  --before 2026-10-24
```

## Checkpoint foundation — 0.4.0a1

The first incremental-sync slice introduces the persistent control-plane contract without yet
calling `users.history.list`.

```text
GmailCheckpoint
├── account_id
├── history_id
├── run_id
├── dataset_version
├── state
├── created_at
├── committed_at
├── invalidated_at
└── invalid_reason
```

Persistent states:

```text
CANDIDATE
COMMITTED
INVALID
```

A candidate is stored separately from the last committed checkpoint. Promotion writes the new
committed checkpoint before removing its run-scoped candidate, so a crash before commit never
advances the durable cursor.

Filesystem layout:

```text
accounts/
└── <account_id>/
    └── checkpoints/
        ├── committed.json
        ├── invalid.json
        └── candidates/
            └── <run_id>.json
```

The checkpoint schema is independently versioned:

```text
checkpoint_schema_version = 1
```

`0.4.0a1` deliberately does **not** introduce Gmail History API calls, event reduction,
tombstones or label mutations. Those begin in `0.4.0b1` and `0.4.0b2`.

## Gmail History incremental — 0.4.0b1

The beta connects the persistent committed checkpoint to Gmail History while preserving the frozen
snapshot and normalized-data contracts.

```text
COMMITTED H100
    ↓
users.history.list(startHistoryId=H100, historyTypes=messageAdded)
    ↓
paged History records
    ↓
RAW history pages
    ↓
MESSAGE_ADDED events
    ↓
messages.get(format=full)
    ↓
immutable RAW messages
    ↓
candidate H125
    ↓
commit H125
```

The input cursor remains the same `H100` across every Gmail History page; pagination is carried by
`pageToken`. The response `historyId` from the final page becomes the output candidate.

CLI:

```bash
gmail-ingest incremental \
  --checkpoint-root .gmail-ingestion-control \
  --artifact-root .gmail-ingestion
```

A committed checkpoint must already exist. The beta deliberately does not invent or silently seed
one from the current Gmail profile.

If Gmail rejects `startHistoryId` as invalid or expired, the connector invalidates the committed
checkpoint and raises a typed `FullResyncRequiredError`. Automatic full-snapshot recovery is provided by the `0.4.0rc1` reliability layer.

Scope of `0.4.0b1`:

```text
MESSAGE_ADDED       supported
MESSAGE_DELETED     supported in 0.4.0b2
LABEL_ADDED         supported in 0.4.0b2
LABEL_REMOVED       supported in 0.4.0b2
```

## Deletions + label mutations — 0.4.0b2

The second beta expands Gmail History from additions-only processing to the four specific change
types exposed by Gmail:

```text
messageAdded
messageDeleted
labelAdded
labelRemoved
```

The connector deliberately uses the specific change arrays rather than the generic
`history.messages[]` field, which Gmail may duplicate.

Reducer flow:

```text
History Record
    ↓
elementary GmailHistoryEvent
    ↓
PENDING persisted event
    ↓
EventReducer
    ├── MESSAGE_ADDED   → messages.get(format=full)
    ├── MESSAGE_DELETED → message tombstone
    ├── LABEL_ADDED     → UPSERT_ACTIVE relation mutation
    └── LABEL_REMOVED   → DEACTIVATE relation mutation
    ↓
effect persisted
    ↓
PROCESSED event
    ↓
candidate checkpoint
    ↓
commit
```

Incremental mutation artifacts stay separate from the frozen normalized schema v1:

```text
gmail.history/<run_id>/
├── raw/
├── history_events/
├── message_tombstones/
├── message_label_mutations/
└── incremental_manifest.json
```

A tombstone records the deleted message ID, deletion history ID, source event and run. A label
mutation records the logical relation operation without rewriting the immutable snapshot dataset.

Exact duplicate event IDs observed again within the same incremental run are reduced only once.

## Incremental reliability — 0.4.0rc1

The release candidate hardens the incremental transaction against process crashes, stale workers
and expired Gmail History cursors.

```text
COMMITTED H100
    ↓
History page 0 ── persist immutable RAW
    ↓
event PENDING
    ↓
effect
    ↓
durable receipt
    ↓
event PROCESSED
    ↓
...
    ↓
CANDIDATE H125
    ↓
READY_TO_COMMIT manifest
    ↓
CAS(H100 → H125)
    ↓
COMMITTED H125
```

Same-run recovery reuses already frozen History pages and already persisted RAW messages instead of
rewriting immutable artifacts. If a process dies after an effect but before the event becomes
`PROCESSED`, the replay recognizes the persisted effect and finishes the event without
re-downloading the message.

Cross-run idempotence uses an account-scoped receipt store:

```text
accounts/<account_id>/history_receipts/<prefix>/<event_id>.json
```

The receipt contract is independently versioned:

```text
history_receipt_schema_version = 1
```

Checkpoint promotion and invalidation use compare-and-swap protection. A stale run cannot promote
its candidate after another run has already advanced the committed cursor.

A missing or expired cursor can recover automatically:

```text
missing / HTTP 404 cursor
    ↓
Full Snapshot
    ↓ only if COMPLETED
bootstrap checkpoint = snapshot.history_start
    ↓
History catch-up
    ↓
final committed checkpoint
```

The CLI enables this recovery path by default:

```bash
gmail-ingest incremental
```

To require strict incremental-only behavior:

```bash
gmail-ingest incremental --no-auto-full-resync
```

The rc1 qualification is automated and synthetic. A real-mailbox Gmail E2E is deliberately kept
as a promotion gate for stable `0.4.0`.

## Stable 0.4.0 live qualification

The real Gmail two-phase promotion gate passed on 2026-10-25 from live-qualified candidate
`8b9a0693a06a922bda8ec728fecee8b2b90e5c8f`.

Observed real History coverage:

```text
MESSAGE_ADDED      5
MESSAGE_DELETED    4
LABEL_ADDED        4
LABEL_REMOVED      5
```

Same-run recovery, cross-run durable receipt replay, zero replay rehydration, checkpoint
advancement and OAuth-secret exclusion all passed. The connector remained `gmail.readonly`.

See `docs/releases/v0.4.0-evidence.md` and
`docs/runbooks/LIVE_INCREMENTAL_QUALIFICATION.md`.

## Normalize with the library

```python
from datetime import UTC, datetime
from uuid import uuid4

from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion import __version__
from gmail_ingestion.normalization import normalize_snapshot
from gmail_ingestion.persistence import NormalizedParquetWriter

normalized = normalize_snapshot(
    account,
    label_payloads,
    message_payloads,
    ingested_at=datetime.now(UTC),
    dataset_version="run-example",
    source_connector_version=__version__,
)

result = NormalizedParquetWriter(
    LocalArtifactStore(".gmail-ingestion")
).write(uuid4(), normalized.tables)
```

The normalization API is deliberately pure with respect to Gmail: callers can replay preserved RAW
payloads without re-contacting the source.

## Quality and packaging

```bash
ruff check .
mypy src
pytest
python -m build
python -m twine check dist/*
```

## Release documentation

- `docs/releases/v0.4.0.md`
- `docs/releases/v0.4.0-evidence.md`
- `docs/releases/COMPATIBILITY-v0.4.0.md`
- `docs/releases/MIGRATION-v0.4.0.md`
- `docs/releases/v0.4.0-stable-promotion.md`
- `docs/runbooks/LIVE_INCREMENTAL_QUALIFICATION.md`
- `docs/releases/v0.4.0rc1.md`
- `docs/releases/v0.4.0rc1-qualification.md`
- `docs/runbooks/INCREMENTAL_RECOVERY.md`
- `docs/releases/v0.4.0b2.md`
- `docs/releases/v0.4.0b2-qualification.md`
- `docs/runbooks/INCREMENTAL_MUTATIONS.md`
- `docs/releases/v0.4.0b1.md`
- `docs/releases/v0.4.0b1-qualification.md`
- `docs/runbooks/INCREMENTAL_HISTORY_SYNC.md`
- `docs/releases/v0.4.0a1.md`
- `docs/runbooks/CHECKPOINT_FOUNDATION.md`
- `docs/releases/v0.3.0.md`
- `docs/releases/v0.3.0-qualification.md`
- `docs/releases/v0.3.0-evidence.md`
- `docs/releases/COMPATIBILITY-v0.3.0.md`
- `docs/releases/MIGRATION-v0.3.0.md`
- `docs/runbooks/FILTERED_INGESTION.md`
- `docs/releases/v0.2.0.md`
- `docs/runbooks/NORMALIZED_DATASET.md`

## Roadmap

```text
0.0.1      Repository Bootstrap                 ✅
0.1.0      Snapshot Foundation Stable           ✅
0.2.0a1    Normalized Data Model                ✅
0.2.0b1    Threads + Relations                  ✅
0.2.0rc1   Normalization Qualification          ✅
0.2.0      Normalized Dataset Stable            ✅ released / frozen
0.3.0a1    Search Filters                        ✅ qualified slice
0.3.0b1    Date Range / Filtered Sync            ✅ qualified
0.3.0      Filtered Ingestion Stable             ✅ stable
0.4.0a1    Checkpoint Foundation                  ✅ qualified
0.4.0b1    Gmail History Incremental               ✅ qualified
0.4.0b2    Deletions + Label Mutations             ✅ qualified
0.4.0rc1   Incremental Reliability                 ✅ qualified
0.4.0      Incremental Sync Stable                 ✅ stable / live-qualified
```

## License

MIT
