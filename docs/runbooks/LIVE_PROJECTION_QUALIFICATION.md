# Live Projection Qualification Runbook — 0.5.0 Stable Gate

## Purpose

Exercise the **real Gmail OAuth/API-client path** and the complete deterministic Current State
projection before promoting `0.5.0rc1` to stable `0.5.0`.

The connector remains read-only:

```text
OAuth scope = https://www.googleapis.com/auth/gmail.readonly
```

Mailbox mutations are performed manually in the Gmail UI between `prepare` and `verify`.

---

## Stable invariant

At one target Gmail History frontier:

```text
CURRENT(previous) + new committed deltas
                    ↓
             incremental projection
                    ↓
                digest A

normalized baseline + complete committed delta chain
                    ↓
               full rebuild
                    ↓
                digest B

digest A == digest B
```

The promoted Current State is authoritative only after this equality passes.

---

## Why the baseline must be full-mailbox

A Current State projection can only claim mailbox completeness when its baseline is complete.

Therefore:

```text
bounded sample → useful for smoke tests
bounded sample → NOT stable-eligible

full normalized mailbox snapshot
        +
complete committed History chain
        ↓
stable-eligible Current State
```

The qualification script refuses `prepare` without `--full-mailbox`.

Use a dedicated Gmail test account when possible.

---

## Isolated qualification roots

Defaults:

```text
.gmail-ingestion-live-0.5.0/
.gmail-ingestion-control-live-0.5.0/
.gmail-ingestion-projection-live-0.5.0/
qualification/v0.5.0-live-session.json
qualification/v0.5.0-live-evidence.json
```

Do not point the script at production state.

The three roots deliberately separate:

```text
artifacts     → RAW / normalized / History evidence
control       → Gmail incremental checkpoint + receipts
projection    → candidate/version/current projection state
```

---

## Phase 0 — Authenticate

If needed:

```bash
gmail-ingest auth
```

No additional OAuth scope is required for `0.5.0`.

---

## Phase 1 — Prepare

Run:

```bash
python scripts/qualify_live_projection.py prepare \
  --full-mailbox
```

Expected logical flow:

```text
empty isolated state
    ↓
ReliableIncrementalSync
    ↓
FullMessageSnapshot
    ↓
normalized baseline COMPLETED
    ↓
bootstrap Gmail History checkpoint
    ↓
History catch-up
    ↓
committed incremental run
    ↓
ProjectCurrentState
    ↓
baseline + catch-up batch
    ↓
incremental digest == rebuild digest
    ↓
initial Current State promoted
```

Expected terminal result:

```text
prepare_gate: PASS
session: qualification/v0.5.0-live-session.json
```

The prepare session is resumable and records opaque run/control identifiers only.

---

## Manual bounded Gmail mutations

After `prepare_gate: PASS`, use Gmail UI to perform:

1. Send one disposable test message to yourself.
2. Add one disposable label to that message.
3. Remove the disposable label.
4. Permanently delete the disposable message.

The fourth step must be a permanent deletion, not only moving to Trash.

This sequence deliberately exercises:

```text
MESSAGE_ADDED
LABEL_ADDED
LABEL_REMOVED
MESSAGE_DELETED
```

### Important add→delete race

The disposable message may already be unavailable through `users.messages.get` when the connector
later hydrates the `MESSAGE_ADDED` History event.

That is valid **only when the same projected chain contains a later `MESSAGE_DELETED` for the
same message**.

The `0.5.0` projection therefore applies:

```text
MESSAGE_ADDED with persisted RAW
→ materialize message

MESSAGE_ADDED without persisted RAW
+ later MESSAGE_DELETED in same projected chain
→ safe no-op for add
→ later delete converges final Current State

MESSAGE_ADDED without persisted RAW
+ no later delete
→ FAIL CLOSED
```

---

## Phase 2 — Verify

Run:

```bash
python scripts/qualify_live_projection.py verify
```

Stable eligibility requires all four event classes by default.

The verify phase performs four layers of qualification.

### A. Real Gmail incremental capture

```text
prepared committed checkpoint
    ↓
users.history.list
    ↓
real Gmail History events
    ↓
effects + receipts + PROCESSED
    ↓
new committed checkpoint
```

### B. Incremental replay safety

The script proves:

```text
same run ID
→ RECOVERED_COMMIT

separate replay checkpoint at original frontier
+ same durable receipt store
→ same deterministic event IDs
→ no repeated business effects
→ messages_hydrated = 0
```

### C. Current State projection

The initial Current State created during prepare is used as predecessor.

```text
CURRENT(previous)
+
new committed delta run
    ↓
new projection candidate
    ↓
label/message/thread reduction
    ↓
referential validation
    ↓
logical digest
    ↓
READY
    ↓
immutable version
    ↓
atomic current pointer
```

### D. Full rebuild equality

The same target is rebuilt independently from:

```text
full normalized baseline
+
prepare catch-up run
+
verify incremental run
```

Required:

```text
incremental logical digest == rebuild logical digest
```

The same projection command is then replayed and must return:

```text
already_current = true
same logical digest
```

---

## Current State mutation checks

The live verifier also checks the final materialized tables without emitting Gmail identifiers.

Required:

```text
current pointer present                         PASS
all MESSAGE_DELETED message IDs absent         PASS
add→delete disposable message converged         PASS
add→remove disposable label relation absent    PASS
```

No message IDs or label IDs are written to the redacted release evidence.

---

## Secret exclusion

Qualification scans:

```text
artifact root
checkpoint/control root
projection root
```

for OAuth access tokens, refresh tokens and client secrets.

Required:

```text
OAuth secrets absent from artifacts   PASS
OAuth secrets absent from control     PASS
OAuth secrets absent from projection  PASS
```

---

## Successful evidence

A successful verify writes:

```text
qualification/v0.5.0-live-evidence.json
```

This local file may contain opaque technical IDs required for reproducibility.

Do not commit it.

Render the redacted release evidence instead:

```bash
python scripts/render_live_projection_evidence.py \
  --candidate-commit "$(git rev-parse HEAD)"
```

Output:

```text
docs/releases/v0.5.0-evidence.md
```

The renderer refuses to emit stable evidence unless:

```text
live_gate_passed = true
stable_eligible = true
all checks = PASS
MESSAGE_ADDED >= 1
MESSAGE_DELETED >= 1
LABEL_ADDED >= 1
LABEL_REMOVED >= 1
projection logical digest == rebuild digest
projection promoted = true
projection replay already_current = true
```

---

## Stable PASS criteria

```text
full normalized baseline completed             PASS
History catch-up committed                     PASS
initial projection promoted                    PASS
initial projection == rebuild                  PASS

real History events observed                   PASS
MESSAGE_ADDED observed                         PASS
MESSAGE_DELETED observed                       PASS
LABEL_ADDED observed                           PASS
LABEL_REMOVED observed                         PASS
all History events PROCESSED                   PASS
checkpoint advanced                            PASS

same-run recovery                              PASS
cross-run receipt replay                       PASS
cross-run messages_hydrated = 0                PASS

projection promoted                            PASS
projection target == committed checkpoint      PASS
incremental projection == full rebuild         PASS
projection replay already_current              PASS
projection replay digest stable                PASS
current pointer == target                      PASS

deleted messages absent from Current State     PASS
add→delete race converged                      PASS
add→remove label race converged                PASS

OAuth secrets absent from artifacts            PASS
OAuth secrets absent from control              PASS
OAuth secrets absent from projection           PASS
```

---

## Stable promotion sequence

After a successful live gate:

```text
0.5.0rc1 candidate CI PASS
    ↓
real Gmail projection gate PASS
    ↓
render + commit redacted evidence
    ↓
bump 0.5.0rc1 → 0.5.0
    ↓
final PR CI
    ↓
merge stable promotion
    ↓
exact-main CI
    ↓
tag v0.5.0
    ↓
GitHub Release
    ↓
publish wheel + sdist + SHA256SUMS-v0.5.0
```

Any substantive projection-semantic code change after the live gate invalidates the evidence and
requires rerunning qualification.
