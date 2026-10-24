# Gmail Ingestion

Reliable, versioned Gmail ingestion built on [PyIngestKit](https://github.com/tawounfouet/pyingestkit).

> **Current development milestone:** `GMAIL_04 — 0.1.0b2 MIME + Attachments + Manifest`

The project turns a Gmail mailbox into a controlled dataset pipeline rather than a one-off email export.

```text
Gmail API
   ↓
Discovery / Extraction
   ↓
RAW Preservation
   ↓
MIME / Attachment Analysis
   ↓
Normalization
   ↓
Validation
   ↓
Dataset Versioning
   ↓
Publication
   ↓
Reconciliation
```

## Current capability

The codebase currently contains four Gmail vertical slices.

### 0.1.0a1 — OAuth + Profile

```text
OAuth Desktop
   ↓
gmail.readonly
   ↓
private local credential store
   ↓
transparent refresh
   ↓
users.getProfile(userId="me")
   ↓
GmailAccount
```

### 0.1.0a2 — Labels

```text
stored Gmail credentials
   ↓
users.labels.list
   ↓
users.labels.get enrichment
   ↓
immutable RAW JSON through PyIngestKit
   ↓
normalized labels Dataset
   ↓
candidate/labels.parquet
```

### 0.1.0b1 — Full Message Snapshot

```text
users.getProfile
   ↓
capture H_START
   ↓
paginated users.messages.list
   ↓
durable message_discovery queue
   ↓
bounded concurrent hydration
   ↓
users.messages.get(format="full")
   ↓
immutable RAW message JSON
   ↓
resumable snapshot by run_id
```

### 0.1.0b2 — MIME + Attachments + Manifest

```text
RAW full message
   ↓
recursive MIME traversal
   ├── headers + duplicate preservation
   ├── parsed mailbox addresses
   ├── text/plain
   ├── text/html
   ├── nested multipart
   ├── charset handling
   └── attachment / inline resource detection
           ↓
metadata-only (default)
           or
optional binary download
           ↓
SHA-256 + separate immutable binary storage
           ↓
candidate/mime/<message_id>.json
           ↓
manifest.json
```

The binary attachment path is deliberately separate from analytical artifacts. Attachment download
is **disabled by default**; metadata-only mode is the safe baseline.

> **Release status:** `0.1.0a1`, `0.1.0a2`, `0.1.0b1` and `0.1.0b2` are implemented and
> CI-qualified once the current milestone pipeline is green. They remain untagged until the
> dedicated real-mailbox acceptance checks are executed.

## Requirements

- Python 3.11+
- Git
- A Google Cloud project with the Gmail API enabled
- An OAuth 2.0 **Desktop app** client JSON downloaded as `credentials.json`

## Installation

```bash
git clone https://github.com/tawounfouet/gmail-ingestion.git
cd gmail-ingestion
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## First Gmail authorization

```bash
gmail-ingest auth
gmail-ingest profile
```

The connector requests only:

```text
https://www.googleapis.com/auth/gmail.readonly
```

## Ingest Gmail labels

```bash
gmail-ingest labels
```

## Create a full snapshot

The default run does **not** download attachment binaries:

```bash
gmail-ingest snapshot
```

Default controls:

```text
include_spam_trash    = false
max_results           = 500
max_workers           = 4
max_attempts          = 5
download_attachments  = false
max_attachment_bytes  = unlimited
```

The snapshot now produces:

```text
runs/gmail/messages/<run_id>/
├── raw/
│   └── message-*-full.json
├── control/
│   └── message_discovery.json
├── candidate/
│   └── mime/
│       └── <message_id>.json
└── manifest.json
```

When binary download is enabled, binaries are isolated under:

```text
runs/gmail/attachments/<run_id>/raw/
└── attachment-*
```

### Download attachment binaries

```bash
gmail-ingest snapshot --download-attachments
```

Apply a binary size ceiling:

```bash
gmail-ingest snapshot \
  --download-attachments \
  --max-attachment-bytes 10485760
```

Oversized binaries are skipped and recorded as warnings in the manifest.

### Resume

```bash
gmail-ingest snapshot --run-id <RUN_UUID>
```

A resumed run:

- does not rediscover messages when discovery was completed;
- does not refetch messages whose immutable RAW already exists;
- does not redownload attachment binaries already stored;
- preserves the original `started_at`;
- refuses to change attachment/spam settings for the same `run_id`.

## Manifest

Each snapshot run writes a JSON manifest containing, among other fields:

```text
schema_version
connector_name
connector_version
run_id
account_id
started_at
completed_at
status
mode
message_format
include_spam_trash
download_attachments
history_start
messages_discovered
messages_fetched
messages_failed
mime_parts
attachments_detected
attachments_fetched
attachments_failed
attachments_skipped
bytes_downloaded
errors
warnings
storage_paths
```

A per-attachment failure does not destroy the entire snapshot. The run is marked `PARTIAL` and the
failure remains structured in `manifest.json`.

See [`docs/runbooks/MIME_ATTACHMENTS_MANIFEST.md`](docs/runbooks/MIME_ATTACHMENTS_MANIFEST.md).

## Credential security

The local credential store is namespaced by opaque `account_id`. OAuth tokens are never included
in RAW messages, MIME artifacts, attachment artifacts, manifests or CLI output.

## CLI

```bash
gmail-ingest --help
gmail-ingest version
gmail-ingest auth --help
gmail-ingest profile --help
gmail-ingest labels --help
gmail-ingest snapshot --help
python -m gmail_ingestion --help
```

## Quality gate

```bash
ruff check .
mypy src
pytest
```

GitHub Actions executes the gate on Python 3.11, 3.12 and 3.13 without real Gmail secrets.

## Roadmap

```text
0.0.1      Repository Bootstrap                       ✅ tagged
0.1.0a1    OAuth + Profile                            ✅ implemented / E2E pending
0.1.0a2    Labels                                     ✅ implemented / E2E pending
0.1.0b1    Full Message Snapshot                      ✅ implemented / E2E pending
0.1.0b2    MIME + Attachments + Manifest              ← current / E2E pending
0.1.0rc1   Snapshot Qualification                     next
0.1.0      Snapshot Foundation Stable
0.2.0      Normalized Dataset
0.3.0      Search / Date Range / Filtered Ingestion
0.4.0      Incremental History Sync
0.5.0      Push / CDC
0.6.0      Reconciliation & Reliability
0.7.0      Dataset Versioning + Object Storage
0.8.0      Communication Analytics Gold
0.9.0      Multi-account & Public Contracts
1.0.0      Production Qualified
```

See [`docs/specs/`](docs/specs/) for the design corpus and [`docs/runbooks/`](docs/runbooks/)
for executable qualification procedures.

## License

MIT
