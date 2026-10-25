# Gmail Ingestion 0.3.0 — Compatibility

## Runtime

```text
Python 3.11  supported
Python 3.12  supported
Python 3.13  supported
```

## PyIngestKit

```text
PyIngestKit v2.1.0
```

remains the pinned ingestion foundation.

## Gmail authorization

The connector continues to use:

```text
https://www.googleapis.com/auth/gmail.readonly
```

No write capability is introduced.

## Normalized data

```text
normalized_schema_version = 1
```

remains unchanged from `0.2.0`.

## Source semantics

`0.3.0` extends `users.messages.list` discovery with explicit scope. Message hydration,
MIME traversal, attachment handling, RAW persistence and normalization contracts remain compatible
with `0.2.0`.

## Manifest

`0.3.0` adds filter/scope evidence to the run manifest. Consumers that ignore unknown manifest
fields remain compatible. Consumers validating the manifest by exact field set must be updated.

## Excluded compatibility surface

Gmail History checkpoints, mutation events and Pub/Sub watch state are not part of `0.3.0`.
