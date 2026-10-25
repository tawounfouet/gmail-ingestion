# Checkpoint Foundation Runbook — 0.4.0a1

## Purpose

Operate and inspect the local Gmail checkpoint control plane introduced in `0.4.0a1`.

This runbook does not cover `users.history.list`; that begins in `0.4.0b1`.

## Storage layout

Given checkpoint root:

```text
.gmail-ingestion-control/
```

one account is stored under:

```text
.gmail-ingestion-control/
└── accounts/
    └── <account_id>/
        └── checkpoints/
            ├── committed.json
            ├── invalid.json
            └── candidates/
                └── <run_id>.json
```

## Read the committed checkpoint

```python
from pathlib import Path

from gmail_ingestion.persistence import LocalCheckpointStore

store = LocalCheckpointStore(Path(".gmail-ingestion-control"))
checkpoint = store.load_committed(account_id)
```

A missing committed checkpoint is represented by `None`.

## Create and persist a candidate

```python
from uuid import uuid4

from gmail_ingestion.domain import GmailCheckpoint

candidate = GmailCheckpoint.candidate(
    account_id=account_id,
    history_id="125",
    run_id=uuid4(),
)
store.save_candidate(candidate)
```

At this point the previous committed checkpoint is unchanged.

## Promote a candidate

```python
committed = store.commit(candidate)
```

Promotion sequence:

```text
persist candidate
   ↓
write committed atomically
   ↓
remove candidate
```

## Discard a failed candidate

```python
store.discard_candidate(account_id, candidate.run_id)
```

This operation must not modify the last committed checkpoint.

## Invalidate a committed checkpoint

```python
invalid = store.invalidate(
    account_id,
    "historyId expired",
)
```

After invalidation:

```text
load_committed(account_id) → None
load_invalid(account_id)   → INVALID checkpoint
```

## Security

Checkpoint files are operational metadata and remain private.

The local store creates directories with owner-only permissions where supported and checkpoint
files with mode `0600`.

Do not place OAuth tokens, message bodies, subjects or attachment contents in checkpoint payloads.

## Recovery rule

When implementing `0.4.0b1`, restart logic must always begin from:

```text
load_committed(account_id)
```

Never resume from an arbitrary candidate unless the application explicitly validates that candidate
for the same run.
