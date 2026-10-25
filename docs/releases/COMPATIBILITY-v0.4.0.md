# Gmail Ingestion 0.4.0 — Compatibility

## Runtime

Qualified CI matrix:

```text
Python 3.11  supported
Python 3.12  supported
Python 3.13  supported
```

Other Python versions may work but are outside the stable CI qualification matrix.

## PyIngestKit

```text
PyIngestKit v2.1.0
```

remains pinned.

## Gmail authorization

The connector continues to use only:

```text
https://www.googleapis.com/auth/gmail.readonly
```

The stable incremental workflow observes Gmail mutations but never performs them.

## Normalized dataset

```text
normalized_schema_version = 1
```

is unchanged from 0.2.0 and 0.3.0. Existing normalized consumers therefore require no schema
migration solely because of the 0.4.0 upgrade.

## New control-plane contracts

```text
checkpoint_schema_version      = 1
history_receipt_schema_version = 1
```

0.4.0 adds checkpoint, History-event, tombstone, label-mutation and receipt artifacts alongside the
existing immutable snapshot artifacts.

## Gmail source semantics

Message hydration remains:

```text
users.messages.get(format=full)
```

History processing uses the specific Gmail collections for message additions/deletions and label
additions/removals. Generic `history.messages[]` is not used as a duplicate event source.

## Filesystem/process boundary

Local checkpoint and receipt persistence uses atomic file replacement and CAS-style logical
predecessor checks. It is qualified for crash/restart protection and stale-run rejection; it is not
a distributed multi-writer transaction coordinator.
