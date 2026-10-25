# Live Incremental Qualification Runbook — 0.4.0 Stable Gate

## Purpose

Exercise the **real Gmail OAuth/API-client path** for the complete incremental reliability flow
before promoting `0.4.0rc1` to stable `0.4.0`.

The connector remains read-only:

```text
OAuth scope = https://www.googleapis.com/auth/gmail.readonly
```

No qualification code sends, labels or deletes mail. The operator performs a small set of
disposable Gmail mutations manually between the two phases.

## Why the stable gate requires a full mailbox baseline

An incremental checkpoint is only meaningful if the baseline behind it is complete.

Therefore:

```text
bounded sample → useful for snapshot smoke tests
bounded sample → NOT a valid full-mailbox incremental baseline
```

The stable incremental gate deliberately refuses to run `prepare` without an explicit
`--full-mailbox` acknowledgement.

Use a dedicated Gmail test account when possible.

## Isolated qualification roots

The default paths are intentionally separate from normal runtime state:

```text
.gmail-ingestion-live-0.4.0/
.gmail-ingestion-control-live-0.4.0/
qualification/v0.4.0-live-session.json
qualification/v0.4.0-live-evidence.json
```

These paths are local qualification data and are gitignored.

Do not point the script at a production checkpoint root.

## Phase 1 — Prepare

Authenticate first if needed:

```bash
gmail-ingest auth
```

Run:

```bash
python scripts/qualify_live_incremental_sync.py prepare \
  --full-mailbox
```

The prepare phase must prove the real product path:

```text
empty isolated checkpoint root
    ↓
ReliableIncrementalSync
    ↓
MissingCommittedCheckpointError
    ↓
FullMessageSnapshot
    ↓ must be COMPLETED
bootstrap = snapshot.history_start
    ↓
History catch-up
    ↓
COMMITTED checkpoint
```

At the start of prepare, the script now persists a resumable state before the Full Snapshot:

```text
snapshot_run_id: <uuid>
prepare_state: qualification/v0.4.0-live-session.json
```

If the process is interrupted, rerun the same prepare command. The persisted `PREPARING`
session reuses the exact Full Snapshot UUID instead of allocating a second full-mailbox export.

For a snapshot created by an older gate that did not persist prepare state, recover the run UUID
from the existing snapshot manifest and resume explicitly:

```bash
python scripts/qualify_live_incremental_sync.py prepare \
  --full-mailbox \
  --snapshot-run-id "<existing-snapshot-run-uuid>"
```

Do not delete the existing artifact root before attempting recovery.

Expected successful terminal result:

```text
prepare_gate: PASS
session: qualification/v0.4.0-live-session.json
```

The session file contains only redacted control evidence such as opaque account ID, run IDs,
history IDs and gate booleans. It contains no Gmail message content or OAuth token.

## Manual bounded Gmail changes

After prepare completes, use the Gmail UI to create the four event types:

1. Send one disposable test message to yourself.
2. Add one disposable label to that message.
3. Remove that label.
4. Permanently delete the disposable test message.

The fourth step must be a permanent deletion, not only a move to Trash, because the connector
qualifies Gmail `messagesDeleted[]`.

Do not perform these actions until the prepare phase has printed PASS.

## Phase 2 — Verify

Run:

```bash
python scripts/qualify_live_incremental_sync.py verify
```

By default, stable eligibility requires all four normalized event types:

```text
MESSAGE_ADDED
MESSAGE_DELETED
LABEL_ADDED
LABEL_REMOVED
```

The verify phase then performs three validations.

### A. Real incremental application

```text
prepared COMMITTED cursor
    ↓
users.history.list
    ↓
real Gmail History events
    ↓
PENDING → effect → receipt → PROCESSED
    ↓
new COMMITTED cursor
```

### B. Same-run recovery

The exact same `run_id` is executed again.

Expected:

```text
committed.run_id == run_id
→ RECOVERED_COMMIT
→ no event reduction repeated
```

### C. Cross-run receipt replay

The script creates a **separate isolated replay checkpoint store** at the original input
`historyId`, while reusing the real receipt store.

That second run re-reads the same Gmail History window and must prove:

```text
same deterministic event IDs
    ↓
receipts already exist
    ↓
effects are not repeated
    ↓
messages_hydrated = 0
    ↓
output checkpoint matches original run
```

## Evidence

Successful verification writes:

```text
qualification/v0.4.0-live-evidence.json
```

The evidence records:

- qualified package version;
- qualification timestamp;
- opaque account ID;
- prepare/full-snapshot run identifiers;
- incremental run identifiers;
- input/output History IDs;
- event counts by normalized event type;
- same-run recovery result;
- cross-run receipt replay counts;
- secret-exclusion checks;
- stable eligibility.

It does **not** record:

- mailbox email address;
- message subject;
- message body;
- sender or recipient addresses;
- OAuth access token;
- refresh token;
- client secret.

## Stable PASS criteria

```text
prepare Full Snapshot recovery          PASS
snapshot catch-up committed             PASS
real History events observed            PASS
MESSAGE_ADDED observed                  PASS
MESSAGE_DELETED observed                PASS
LABEL_ADDED observed                    PASS
LABEL_REMOVED observed                  PASS
all History events PROCESSED            PASS
checkpoint advanced                     PASS
same-run RECOVERED_COMMIT               PASS
cross-run receipts replayed             PASS
cross-run messages_hydrated = 0         PASS
cross-run output checkpoint stable      PASS
OAuth secrets absent from artifacts     PASS
OAuth secrets absent from control state PASS
```

Only after this gate passes should the package be promoted to `0.4.0`.

## Render redacted release evidence

After a successful verify phase, generate the committable redacted release evidence:

```bash
python scripts/render_live_incremental_evidence.py \
  --candidate-commit "$(git rev-parse HEAD)"
```

The renderer refuses to produce stable evidence unless:

```text
live_gate_passed = true
stable_eligible = true
all checks = PASS
MESSAGE_ADDED >= 1
MESSAGE_DELETED >= 1
LABEL_ADDED >= 1
LABEL_REMOVED >= 1
```

Output:

```text
docs/releases/v0.4.0-evidence.md
```

The rendered document excludes account ID, History IDs, run IDs and Gmail content.

## If verify fails because one event type is missing

Do not weaken the stable release criterion.

Keep the existing session if the committed cursor has not already moved unexpectedly, create a new
fresh qualification session if necessary, perform the missing Gmail mutation after prepare, and
rerun the two-phase gate.

For exploratory smoke testing only, `--require-event-type` may be used to request a subset, but
such evidence is not stable-eligible unless all four event types are required.

## Stable promotion sequence

```text
live gate PASS
→ commit redacted evidence summary
→ bump 0.4.0rc1 → 0.4.0
→ final CI
→ merge PR #4
→ rebuild exact merge SHA
→ tag v0.4.0
→ GitHub Release
→ publish wheel + sdist + SHA256SUMS-v0.4.0
```
