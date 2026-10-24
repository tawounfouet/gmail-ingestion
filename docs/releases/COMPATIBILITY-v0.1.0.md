# Gmail Ingestion 0.1.0 — Compatibility

## Python

Supported:

```text
Python 3.11
Python 3.12
Python 3.13
```

## PyIngestKit

The stable Snapshot Foundation pins:

```text
PyIngestKit v2.1.0
```

The dependency is intentionally tag-pinned rather than following `main`.

## Gmail API

The connector uses Gmail API v1 and the exact OAuth scope:

```text
https://www.googleapis.com/auth/gmail.readonly
```

No Gmail write scope is part of 0.1.0.

## Stable contracts in 0.1.0

The stable release preserves:

- opaque local `account_id`;
- immutable RAW message JSON;
- label candidate Parquet schema introduced in 0.1.0a2;
- message discovery resume semantics;
- MIME technical analysis artifacts;
- separate attachment binary storage;
- manifest schema version 1.

## Not promised by 0.1.0

The following are later milestones and are not stable contracts of this release:

- normalized analytical message/thread tables;
- search/date-range ingestion;
- Gmail History incremental sync;
- Pub/Sub CDC;
- reconciliation engine;
- object-storage publication;
- production SLOs.
