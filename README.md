# Gmail Ingestion

Reliable, versioned Gmail ingestion built on [PyIngestKit](https://github.com/tawounfouet/pyingestkit).

> **Current release target:** `v0.2.0 — Normalized Dataset Stable`

`0.2.0` preserves the qualified `0.1.0` Gmail snapshot path and adds the first stable
analytical contract.

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

## Release status

The pre-promotion candidate is automatically qualified on:

```text
Python 3.11       PASS
Python 3.12       PASS
Python 3.13       PASS
pytest            46/46 PASS
Ruff              PASS
mypy strict       PASS
wheel + sdist     PASS
twine check       PASS
clean install     PASS
PyIngestKit       v2.1.0 verified
```

The source-extraction layer remains the same qualified `gmail.readonly` Snapshot Foundation
released in `0.1.0`. The `0.2.0` transformation path performs no Gmail API call.

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
git checkout v0.2.0
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
gmail-ingest version
```

Expected:

```text
0.2.0
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

- `docs/releases/v0.2.0.md`
- `docs/releases/v0.2.0-qualification.md`
- `docs/releases/COMPATIBILITY-v0.2.0.md`
- `docs/releases/MIGRATION-v0.2.0.md`
- `docs/runbooks/NORMALIZED_DATASET.md`

## Roadmap

```text
0.0.1      Repository Bootstrap                 ✅
0.1.0      Snapshot Foundation Stable           ✅
0.2.0a1    Normalized Data Model                ✅
0.2.0b1    Threads + Relations                  ✅
0.2.0rc1   Normalization Qualification          ✅
0.2.0      Normalized Dataset Stable            candidate
0.3.0      Search / Date Range / Filtered       next
```

## License

MIT
