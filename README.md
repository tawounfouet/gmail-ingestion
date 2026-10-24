# Gmail Ingestion

Reliable, versioned Gmail ingestion built on [PyIngestKit](https://github.com/tawounfouet/pyingestkit).

> **Current milestone:** `GMAIL_01 — 0.1.0a1 OAuth + Profile`

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

`0.1.0a1` establishes the first real Gmail source connection:

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

No Gmail write scope is requested. Message extraction starts in a later milestone.

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

The command opens Google's consent flow and requests exactly:

```text
https://www.googleapis.com/auth/gmail.readonly
```

After consent, the connector verifies the mailbox with `users.getProfile`, assigns a local opaque
`account_id`, stores the OAuth credential in the private account namespace, and marks the account as
active.

Use a custom client file or credential directory when required:

```bash
gmail-ingest auth \
  --client-secrets /secure/path/credentials.json \
  --credential-dir ~/.local/share/gmail-ingestion/credentials
```

## Retrieve the Gmail profile

```bash
gmail-ingest profile
```

The active account is used by default. An explicit account can also be selected:

```bash
gmail-ingest profile --account-id acct_0123456789abcdef01234567
```

JSON output is available for automation:

```bash
gmail-ingest profile --json
```

The normalized profile contains `account_id`, `email_address`, `messages_total`,
`threads_total`, and `history_id`.

Expired access tokens are refreshed transparently when a refresh token is available. A revoked or
unusable grant fails explicitly and requires `gmail-ingest auth` again.

## Credential security

The local store is namespaced by opaque `account_id`. On POSIX-compatible systems, credential
directories are restricted to `0700` and files to `0600`. OAuth tokens are never included in the
`GmailAccount` domain object or normal CLI output.

The repository ignores `.env`, `credentials.json`, `client_secret*.json`, `token*.json`,
`tokens/`, and `secrets/`.

## CLI

```bash
gmail-ingest --help
gmail-ingest version
gmail-ingest auth --help
gmail-ingest profile --help
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
0.0.1      Repository Bootstrap                       ✅
0.1.0a1    OAuth + Profile                            ← current
0.1.0a2    Labels
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

See [`docs/specs/`](docs/specs/) for the design corpus.

## License

MIT
