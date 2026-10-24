# Gmail Ingestion

Reliable, versioned Gmail ingestion built on [PyIngestKit](https://github.com/tawounfouet/pyingestkit).

> **Current development milestone:** `GMAIL_03 — 0.1.0b1 Full Message Snapshot`

The project turns a Gmail mailbox into a controlled dataset pipeline rather than a one-off email export.

```text
Gmail API
   ↓
Discovery / Extraction
   ↓
RAW Preservation
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

The codebase currently contains three Gmail vertical slices.

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
14-column normalized labels Dataset
   ↓
candidate/labels.parquet
   +
candidate/labels.schema.json
```

### 0.1.0b1 — Full Message Snapshot

```text
stored Gmail credentials
   ↓
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
PyIngestKit RetryPolicy
   ↓
immutable RAW message JSON
   ↓
resumable snapshot by run_id
```

The snapshot control state is persisted before hydration and updated as messages are fetched. A
restart with the same `run_id` skips confirmed messages, recovers already-written immutable RAW,
and retries only unresolved records.

MIME parsing, attachment extraction and the auditable snapshot manifest belong to the next
milestone, `0.1.0b2`.

> **Release status:** `0.1.0a1`, `0.1.0a2` and `0.1.0b1` are implemented and CI-qualified.
> They remain untagged until their dedicated real-mailbox Gmail API acceptance checks are executed.

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

Place the Google OAuth Desktop client JSON in the repository working directory as `credentials.json`.
The file is ignored by Git.

```bash
gmail-ingest auth
```

The command requests exactly:

```text
https://www.googleapis.com/auth/gmail.readonly
```

After consent, the connector verifies the mailbox with `users.getProfile`, assigns a local opaque
`account_id`, stores the OAuth credential in the private account namespace, and marks the account
as active.

Use a custom client file or credential directory when required:

```bash
gmail-ingest auth \
  --client-secrets /secure/path/credentials.json \
  --credential-dir ~/.local/share/gmail-ingestion/credentials
```

## Retrieve the Gmail profile

```bash
gmail-ingest profile
gmail-ingest profile --json
```

The normalized profile contains `account_id`, `email_address`, `messages_total`,
`threads_total`, and `history_id`.

## Ingest Gmail labels

```bash
gmail-ingest labels
```

The default labels flow retrieves the label catalogue, enriches each label with
`users.labels.get`, persists source JSON as immutable RAW artifacts through PyIngestKit, normalizes
the target schema, and writes Parquet plus a JSON schema artifact under one run ID.

List-only mode:

```bash
gmail-ingest labels --no-details
```

## Create a full message snapshot

Start a new full snapshot:

```bash
gmail-ingest snapshot
```

Defaults follow the project requirements:

```text
include_spam_trash = false
max_results        = 500
max_workers        = 4
max_attempts       = 5
```

The command persists discovered message IDs before hydration, traverses all discovery pages, fetches
messages with `format=full`, retries safe acquisition failures through PyIngestKit, and stores every
successful source response as immutable RAW JSON.

Resume an interrupted or partially failed snapshot with the emitted run UUID:

```bash
gmail-ingest snapshot --run-id <RUN_UUID>
```

Include Spam and Trash explicitly when required:

```bash
gmail-ingest snapshot --include-spam-trash
```

Choose another artifact root:

```bash
gmail-ingest snapshot --artifact-root /secure/data/gmail-ingestion
```

See [`docs/runbooks/FULL_MESSAGE_SNAPSHOT.md`](docs/runbooks/FULL_MESSAGE_SNAPSHOT.md) for the
real-mailbox acceptance procedure.

## Credential security

The local store is namespaced by opaque `account_id`. On POSIX-compatible systems, credential
directories are restricted to `0700` and files to `0600`. OAuth tokens are never included in
normal domain objects, datasets, RAW Gmail payloads, control artifacts, or CLI output.

The repository ignores `.env`, `credentials.json`, `client_secret*.json`, `token*.json`,
`tokens/`, and `secrets/`.

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

GitHub Actions executes the quality gate on Python 3.11, 3.12 and 3.13 without real Gmail secrets.

## Roadmap

```text
0.0.1      Repository Bootstrap                       ✅ tagged
0.1.0a1    OAuth + Profile                            ✅ implemented / E2E pending
0.1.0a2    Labels                                     ✅ implemented / E2E pending
0.1.0b1    Full Message Snapshot                      ← current / E2E pending
0.1.0b2    MIME + Attachments + Manifest              next
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
