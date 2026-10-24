# Gmail Ingestion

Reliable, versioned Gmail ingestion built on [PyIngestKit](https://github.com/tawounfouet/pyingestkit).

> **Release branch:** `release/v0.1.0 — Snapshot Foundation Stable`

The release branch freezes the qualified Snapshot Foundation without adding Gmail semantics.

```text
OAuth Desktop
   ↓
Profile
   ↓
Labels
   ↓
Full Message Snapshot
   ↓
Immutable RAW
   ↓
MIME / Attachment Analysis
   ↓
Manifest v1
```

## Release status

The stable candidate is automatically qualified:

```text
Python 3.11       PASS
Python 3.12       PASS
Python 3.13       PASS
pytest            37/37 PASS
Ruff              PASS
mypy strict       PASS
wheel + sdist     PASS
twine check       PASS
clean install     PASS
PyIngestKit       v2.1.0 verified
```

The **only remaining stable-release gate** is a real Gmail test mailbox executed through this
package's own Desktop OAuth and Gmail API client path. The connected Gmail integration used for
source-shape validation is intentionally not treated as a substitute.

## Stable Snapshot Foundation

### OAuth + Profile

- exact `gmail.readonly` scope;
- private per-account local credentials;
- transparent refresh;
- mailbox identity verification.

### Labels

- `users.labels.list`;
- optional `users.labels.get` enrichment;
- immutable RAW;
- normalized labels Parquet candidate.

### Messages

- paginated `users.messages.list`;
- durable discovery queue;
- bounded concurrency and retry;
- `users.messages.get(format="full")`;
- immutable RAW;
- resumable execution.

### MIME + Attachments

- recursive multipart traversal;
- text/plain and text/html;
- charset handling;
- duplicate header preservation;
- attachment and inline-resource detection;
- metadata-only default;
- optional binary download with SHA-256 and size limit;
- separate binary storage.

### Manifest

Every snapshot records versioned run evidence, counts, H_START, errors, warnings and storage paths.

## Requirements

- Python 3.11, 3.12 or 3.13;
- Git;
- Google Cloud project with Gmail API enabled;
- OAuth 2.0 Desktop client JSON as `credentials.json`.

The release pins PyIngestKit to:

```text
v2.1.0
```

## Install

```bash
git clone https://github.com/tawounfouet/gmail-ingestion.git
cd gmail-ingestion
git checkout release/v0.1.0
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
gmail-ingest version
```

Expected:

```text
0.1.0
```

## Authorize

```bash
gmail-ingest auth
gmail-ingest profile
```

## Labels

```bash
gmail-ingest labels
```

## Snapshot

Safe metadata-only default:

```bash
gmail-ingest snapshot
```

Optional attachment binaries:

```bash
gmail-ingest snapshot \
  --download-attachments \
  --max-attachment-bytes 10485760
```

Resume:

```bash
gmail-ingest snapshot \
  --run-id <RUN_UUID> \
  --download-attachments \
  --max-attachment-bytes 10485760
```

## Stable live qualification

Use a dedicated Gmail test mailbox:

```bash
gmail-ingest auth
python scripts/qualify_live_snapshot_foundation.py \
  --download-attachments \
  --require-attachment \
  --max-attachment-bytes 10485760
```

Required output:

```text
live_gate: PASS
```

The script emits redacted evidence at:

```text
qualification/v0.1.0-live-evidence.json
```

Do not commit `credentials.json`, local token files, mailbox RAW or qualification artifact roots.

## Distribution candidate

```text
gmail_ingestion-0.1.0-py3-none-any.whl
SHA-256 da9cc008d257274b4ea1c6188121aa75acb1939e92697555b28fcee82d5a3bc1

gmail_ingestion-0.1.0.tar.gz
SHA-256 a77c708076ffe02f3989edd65399c7dcfc327eb0cf88868991d7f1b02605251f
```

## Quality and packaging

```bash
ruff check .
mypy src
pytest
python -m build
python -m twine check dist/*
```

## Release documentation

- `docs/releases/v0.1.0-qualification.md`
- `docs/releases/v0.1.0-evidence.md`
- `docs/releases/v0.1.0.md`
- `docs/releases/COMPATIBILITY-v0.1.0.md`
- `docs/releases/MIGRATION-v0.1.0.md`

## Roadmap

```text
0.0.1      Repository Bootstrap                 ✅
0.1.0a1    OAuth + Profile                      ✅
0.1.0a2    Labels                               ✅
0.1.0b1    Full Message Snapshot                ✅
0.1.0b2    MIME + Attachments + Manifest        ✅
0.1.0rc1   Snapshot Qualification               ✅ automated qualified
0.1.0      Snapshot Foundation Stable           ← automated GO / live E2E pending
0.2.0      Normalized Dataset                   next after stable publication
```

## License

MIT
