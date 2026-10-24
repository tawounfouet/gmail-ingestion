# Gmail Ingestion

Reliable, versioned Gmail ingestion built on [PyIngestKit](https://github.com/tawounfouet/pyingestkit).

> **Current development milestone:** `GMAIL_02 — 0.1.0a2 Labels`

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

The codebase currently contains two Gmail vertical slices:

```text
0.1.0a1 — OAuth + Profile
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

0.1.0a2 — Labels
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

No Gmail write scope is requested. Message extraction starts in `0.1.0b1`.

> Release note: `0.1.0a1` and `0.1.0a2` are implemented and CI-qualified, but are not tagged
> until the corresponding real-mailbox OAuth/API acceptance checks have been executed.

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

Expired access tokens are refreshed transparently when a refresh token is available. A revoked or
unusable grant fails explicitly and requires `gmail-ingest auth` again.

## Ingest Gmail labels

After OAuth is established:

```bash
gmail-ingest labels
```

The default labels flow retrieves the label catalogue, enriches each label with
`users.labels.get`, persists source JSON as immutable RAW artifacts through PyIngestKit, normalizes
the target schema, and writes Parquet plus a JSON schema artifact under one run ID.

Use list-only mode when detailed counters and colors are not needed:

```bash
gmail-ingest labels --no-details
```

Choose another artifact root when required:

```bash
gmail-ingest labels --artifact-root /secure/data/gmail-ingestion
```

The default root is `.gmail-ingestion/`, which is ignored by Git.

## Credential security

The local store is namespaced by opaque `account_id`. On POSIX-compatible systems, credential
directories are restricted to `0700` and files to `0600`. OAuth tokens are never included in
normal domain objects, datasets, RAW Gmail payloads, or CLI output.

The repository ignores `.env`, `credentials.json`, `client_secret*.json`, `token*.json`,
`tokens/`, and `secrets/`.

## CLI

```bash
gmail-ingest --help
gmail-ingest version
gmail-ingest auth --help
gmail-ingest profile --help
gmail-ingest labels --help
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
0.1.0a2    Labels                                     ← current / E2E pending
0.1.0b1    Full Message Snapshot
0.1.0b2    MIME + Attachments + Manifest
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
