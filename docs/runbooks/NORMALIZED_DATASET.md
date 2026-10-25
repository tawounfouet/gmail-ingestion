# Normalized Dataset Runbook

## Purpose

Build the Gmail normalized schema v1 from already retrieved source payloads and persist deterministic
Parquet artifacts.

## Preconditions

- a verified `GmailAccount`;
- Gmail label payloads;
- Gmail `messages.get(format="full")` payloads preserved from the source layer;
- a timezone-aware normalization timestamp;
- a dataset version identifier;
- an artifact store.

## Library flow

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

## Produced tables

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

## Produced evidence

The writer emits:

```text
candidate/normalized/<table>.parquet
candidate/normalized/schema-v1.json
candidate/normalized/quality.json
```

## Replay rule

Keep `ingested_at`, `dataset_version` and source payloads fixed when testing deterministic logical
equality. Physical Parquet metadata is not the contract; reloaded logical rows are.

## Failure rule

Do not publish normalized outputs when a quality check fails. Fix or quarantine the source/object
issue, then rebuild from RAW.
