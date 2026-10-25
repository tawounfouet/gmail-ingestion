# Gmail Ingestion 0.2.0 — Compatibility

## Python

Supported:

```text
Python 3.11
Python 3.12
Python 3.13
```

## PyIngestKit

Pinned:

```text
PyIngestKit v2.1.0
```

## Gmail source layer

Unchanged from 0.1.0:

```text
Gmail API v1
https://www.googleapis.com/auth/gmail.readonly
```

0.2.0 adds no Gmail write capability.

## Stable data contract

```text
normalized_schema_version = 1
```

Stable tables:

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

A future change to table grain, logical key semantics, or an incompatible field type requires an
explicit schema migration and normalized schema version review.
