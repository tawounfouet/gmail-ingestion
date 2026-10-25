# Gmail Ingestion

Reliable, versioned Gmail ingestion built on [PyIngestKit](https://github.com/tawounfouet/pyingestkit).

> **Stable release:** `v0.3.0 — Filtered Ingestion Stable`  
> **Current development target:** `v0.4.0 — Incremental History Sync`

`0.3.0` is the qualified stable filtered-ingestion release. It adds deterministic Gmail search,
label and date-range scoping while preserving normalized schema v1 and the `0.2.0` normalized-data foundation.

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
0.4.0      Incremental History Sync               next
```

## License

MIT
