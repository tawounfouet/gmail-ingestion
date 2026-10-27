# Changelog

All notable changes to Gmail Ingestion are documented in this file.

The project follows Semantic Versioning with pre-release milestones during the pre-1.0 phase.


## 0.5.0rc4 — 2026-10-27 — Legacy RAW Baseline Bootstrap

### Added

- `gmail-ingest bootstrap-baseline` for completed 0.4 Full Snapshot artifacts;
- local-only RAW → projection-baseline normalization with no Gmail message API calls;
- streaming Parquet generation to avoid loading full-mailbox RAW into memory;
- derived `gmail.projection_baseline` artifact namespace;
- baseline completion manifest published after all required Parquet tables;
- idempotent reuse of complete baseline evidence;
- fail-closed validation for incomplete discovery, failed fetches, missing RAW and partial evidence;
- `NormalizedBaselineReader` fallback from full `gmail.normalized` datasets to the derived
  projection baseline;
- compatibility tests proving a legacy RAW snapshot can feed the existing `project` reader.

### Compatibility problem solved

```text
0.4 Full Snapshot recovery
→ RAW Gmail snapshot + checkpoint
→ no automatic gmail.normalized Parquet

0.5 projection
→ requires normalized baseline

RC4
→ derives projection-compatible normalized baseline locally
→ reuses the existing Full Snapshot RAW
```

### Preserved contracts

```text
normalized_schema_version      = 1
checkpoint_schema_version      = 1
history_receipt_schema_version = 1
projection_schema_version      = 1
OAuth scope                    = gmail.readonly
PyIngestKit                    = 2.1.0
```



## 0.5.0rc3 — 2026-10-26 — Unavailable Add/Delete Race Hardening

### Fixed

- projection no longer fails when Gmail History contains a `MESSAGE_ADDED` whose full RAW message
  is already unavailable **and** a later `MESSAGE_DELETED` for the same message exists in the
  projected event chain;
- the unavailable add is treated as a safe no-op only because the later delete proves the message
  is absent from the target Current State;
- `MESSAGE_ADDED` without persisted RAW and without a later matching delete remains fail-closed;
- event ordering for this decision is deterministic by numeric `history_id`, event ordinal and
  deterministic event ID;
- regression coverage proves the final messages/threads Current State converges correctly.

### Safety invariant

```text
MESSAGE_ADDED without RAW
        +
later MESSAGE_DELETED for same message
        ↓
safe skip of unavailable add
        ↓
delete reducer
        ↓
target Current State excludes message

MESSAGE_ADDED without RAW
        +
no later matching delete
        ↓
FAIL CLOSED
```

### Preserved contracts

```text
normalized_schema_version      = 1
checkpoint_schema_version      = 1
history_receipt_schema_version = 1
projection_schema_version      = 1
OAuth scope                    = gmail.readonly
RAW message hydration          = format=full
PyIngestKit                    = 2.1.0
```



## 0.5.0rc2 — 2026-10-26 — Projection Operationalization

### Added

- `LocalProjectionBatchReader` converting committed 0.4 incremental manifests + PROCESSED
  History-event files into trusted `ProjectionDeltaBatch` instances;
- fail-closed rejection of incomplete, uncommitted, wrong-account or inconsistent run evidence;
- `ProjectCurrentState` use case consuming explicit ordered incremental run IDs;
- automatic incremental suffix selection when a Current projection already exists;
- mandatory full-chain rebuild qualification before every new projection promotion;
- idempotent `already_current` behavior when the requested target is already published;
- `gmail-ingest project` CLI;
- `--candidate-only/--promote` publication control;
- explicit `--baseline-run-id`, repeatable `--delta-run-id`, artifact root and projection root;
- CI smoke coverage for the new project command.

### Operational flow

```text
qualified normalized baseline run
+
ordered committed incremental run IDs
        ↓
LocalProjectionBatchReader
        ↓
validate complete baseline→target chain
        ↓
select Current→target incremental suffix
        ↓
materialize incremental candidate
        ↓
full baseline→target rebuild proof
        ↓
logical digests equal
        ↓
promote atomically
        ↓
CURRENT
```

### Design boundary

The project command does **not** call Gmail and does **not** require OAuth.

```text
0.4 capture artifacts
        ↓
0.5 materialization
```

remain separate concerns.

### Preserved contracts

```text
normalized_schema_version      = 1
checkpoint_schema_version      = 1
history_receipt_schema_version = 1
projection_schema_version      = 1
OAuth scope                    = gmail.readonly
RAW message hydration          = format=full
PyIngestKit                    = 2.1.0
```

### Stable gate remaining

- bounded real Gmail scenario producing qualified 0.4 artifacts;
- `gmail-ingest project` against those real artifacts;
- rebuild digest equality;
- redacted release evidence;
- stable version-only promotion to `0.5.0`;
- exact-main stable CI and release artifacts.



## 0.5.0rc1 — 2026-10-26 — Projection Reliability & Rebuild Qualification

### Reliability model

- explicit `ProjectionDeltaBatch` contract for committed incremental frontier transitions;
- delta-chain continuity validation without assuming Gmail `historyId` values are contiguous;
- fail-closed `ProjectionGapError` when the batch chain does not start, connect or terminate at the requested frontier;
- second-generation projection now starts from the immutable predecessor Current State rather than incorrectly restarting from the original baseline with only new deltas;
- deterministic full-rebuild digest path from the normalized baseline and the complete committed delta chain;
- `ProjectionConvergenceResult` proving incremental and rebuild logical equality;
- fail-closed `ProjectionConvergenceError` on digest divergence;
- logical digest excludes physical lineage/timing fields that do not define Gmail business state;
- recovery of a complete `BUILDING` candidate from persisted Parquet + projection-data manifest without replaying RAW;
- corrupt persisted candidate digest is rejected and the candidate is marked `FAILED`.

### Central invariant

```text
logical_digest(
    predecessor current state
    + new committed deltas
)
==
logical_digest(
    normalized baseline
    + complete committed delta chain
)
```

at the same target Gmail History frontier.

### Crash/recovery boundary

```text
write candidate Parquet tables
        ↓
write projection-data.json LAST
        ↓
crash may occur
        ↓
restart
        ↓
manifest present?
   ├── no  → deterministic rebuild of candidate
   └── yes → reload + digest validation
                 ↓
               READY
```

### Preserved contracts

```text
normalized_schema_version      = 1
checkpoint_schema_version      = 1
history_receipt_schema_version = 1
projection_schema_version      = 1
OAuth scope                    = gmail.readonly
RAW message hydration          = format=full
PyIngestKit                    = 2.1.0
```

### Stable gate remaining after rc1

- bounded real-mailbox projection qualification;
- stable release evidence;
- exact-main stable CI/rebuild;
- version promotion to `0.5.0`.



## 0.5.0b2 — 2026-10-26 — Relations & Thread Projection

### Added

- `LABEL_ADDED` materialization as an active current `message_labels` relation;
- `LABEL_REMOVED` materialization as physical exclusion from the current relation set;
- no orphan label relation when the referenced current message no longer exists;
- `CURRENT_PROJECTION_TABLES` extending message projection with `threads`;
- deterministic thread recomputation from current messages and recipients;
- thread history frontier derived from current message/history events;
- current thread removal when its last message disappears;
- message-to-thread referential-integrity validation before projection publication;
- logical digest extended to include relational/thread projection tables;
- candidate/version Parquet persistence for the complete b2 table set;
- counters for `LABEL_ADDED` and `LABEL_REMOVED`;
- end-to-end test covering all four qualified Gmail History event classes in one projection.

### Event coverage

```text
MESSAGE_ADDED      MATERIALIZED
MESSAGE_DELETED    MATERIALIZED
LABEL_ADDED        MATERIALIZED
LABEL_REMOVED      MATERIALIZED
```

### Current-state flow

```text
normalized baseline
+
ordered processed deltas
        ↓
message + relation reducer
        ↓
current messages / dependent rows
        ↓
thread recomputation
        ↓
referential validation
        ↓
logical digest
        ↓
READY candidate
        ↓
immutable version
        ↓
atomic current pointer
```

### Preserved contracts

```text
normalized_schema_version      = 1
checkpoint_schema_version      = 1
history_receipt_schema_version = 1
projection_schema_version      = 1
OAuth scope                    = gmail.readonly
RAW message hydration          = format=full
PyIngestKit                    = 2.1.0
```

### Deferred to 0.5.0rc1

- crash/recovery qualification across projection build and promotion boundaries;
- explicit gap-detection qualification;
- full rebuild command/path qualification;
- incremental projection vs full rebuild digest equality qualification;
- stale-writer and candidate-recovery stress qualification.



## 0.5.0b1 — 2026-10-26 — Message Delta Materialization

### Added

- pure `MessageProjectionReducer` applying ordered `MESSAGE_ADDED` / `MESSAGE_DELETED` deltas;
- deterministic History ordering by numeric `history_id`, event ordinal and deterministic event ID;
- duplicate event suppression and idempotent replay;
- `NormalizedBaselineReader` for normalized schema v1 Parquet baselines;
- persisted RAW-only normalization path for `MESSAGE_ADDED` with **no Gmail API call**;
- physical exclusion of `MESSAGE_DELETED` messages from the materialized current-state tables;
- replacement/removal of message-dependent rows across:
  `messages`, `message_headers`, `message_recipients`, `message_labels`,
  `mime_parts` and `attachments`;
- deterministic logical projection digest;
- candidate Parquet projection persistence and projection-data manifest;
- candidate data promotion into immutable projection versions before atomic `current.json` switch;
- fail-closed handling for missing RAW, unprocessed events and out-of-frontier History events;
- regression tests for addition, deletion, ordering, duplicate replay, immutable promotion and failure paths.

### Message projection contract

```text
NORMALIZED BASELINE
        +
PROCESSED MESSAGE DELTAS
        ↓
deterministic reducer
        ↓
candidate current message state
        ↓
logical digest + validation
        ↓
READY
        ↓
immutable projection version
        ↓
atomic current pointer
```

### Event coverage in this beta

```text
MESSAGE_ADDED      MATERIALIZED
MESSAGE_DELETED    MATERIALIZED
LABEL_ADDED        deferred to 0.5.0b2
LABEL_REMOVED      deferred to 0.5.0b2
thread recompute   deferred to 0.5.0b2
```

### Preserved contracts

```text
normalized_schema_version      = 1
checkpoint_schema_version      = 1
history_receipt_schema_version = 1
projection_schema_version      = 1
OAuth scope                    = gmail.readonly
RAW message hydration          = format=full
PyIngestKit                    = 2.1.0
```



## 0.5.0a1 — 2026-10-26 — Projection Foundation

### Added

- independent `projection_schema_version = 1`;
- deterministic `ProjectionIdentity` derived from account, baseline, target History frontier and schema;
- immutable `ProjectionPlan`, `ProjectionState`, `ProjectionManifest` and `CurrentPointer`;
- explicit `NEW → BUILDING → READY` candidate lifecycle plus persisted `FAILED` state;
- `ProjectionStore` port and atomic filesystem `LocalProjectionStore`;
- account-scoped candidate persistence and immutable promoted projection versions;
- atomic `current.json` publication after the immutable version is safely persisted;
- predecessor-based compare-and-swap protection rejecting stale projection writers;
- idempotent candidate creation and promotion recovery;
- structural validation through `ProjectionValidator`;
- orchestration through `ProjectionFoundationService`;
- regression tests for deterministic identity, lifecycle, reload, atomic publication, failure,
  continuity and stale-writer rejection.

### Projection control-plane contract

```text
CURRENT
   ↓
plan
   ↓
create candidate
   ↓
BUILDING
   ↓
validate
   ↓
READY
   ↓
persist immutable version
   ↓
atomically replace current pointer
   ↓
NEW CURRENT
```

### Preserved contracts

```text
normalized_schema_version      = 1
checkpoint_schema_version      = 1
history_receipt_schema_version = 1
projection_schema_version      = 1
OAuth scope                    = gmail.readonly
RAW message hydration          = format=full
PyIngestKit                    = 2.1.0
```

### Deliberately deferred to 0.5.0b1 / 0.5.0b2

This alpha does **not** yet apply Gmail business mutations to the materialized current state.

```text
MESSAGE_ADDED      → 0.5.0b1
MESSAGE_DELETED    → 0.5.0b1
LABEL_ADDED        → 0.5.0b2
LABEL_REMOVED      → 0.5.0b2
thread recompute   → 0.5.0b2
```


## 0.4.0 — 2026-10-25 — Incremental Sync Stable

### Stable capability

- committed per-account Gmail History checkpoints with explicit candidate/commit/invalidate states;
- incremental `users.history.list` processing for message additions, deletions and label mutations;
- deterministic event IDs and append-style mutation/tombstone artifacts;
- durable account-scoped receipts for cross-run idempotence;
- same-run replay of frozen History pages and persisted effects;
- compare-and-swap checkpoint promotion/invalidation;
- automatic `Full Snapshot → bootstrap checkpoint → History catch-up` recovery;
- resumable full-mailbox qualification snapshots and resumable post-bootstrap catch-up;
- graceful handling of malformed individual Gmail MIME headers as warnings;
- graceful handling of History `MESSAGE_ADDED` events whose message is already permanently deleted
  when hydration occurs.

### Stable contracts

```text
normalized_schema_version      = 1
checkpoint_schema_version      = 1
history_receipt_schema_version = 1
OAuth scope                    = gmail.readonly
RAW message hydration          = format=full
PyIngestKit                    = 2.1.0
```

### Real Gmail qualification

The external Gmail OAuth/API-client gate passed on 2026-10-25 from candidate
`8b9a0693a06a922bda8ec728fecee8b2b90e5c8f`.

```text
MESSAGE_ADDED      5
MESSAGE_DELETED    4
LABEL_ADDED        4
LABEL_REMOVED      5

checkpoint advancement                       PASS
all events processed                         PASS
same-run recovery                            PASS
cross-run durable receipt replay             PASS
cross-run message rehydration                0
OAuth secrets absent from artifacts/control  PASS
```

The live-qualified code candidate also passed CI #117 with Python 3.11/3.12/3.13, Ruff, mypy
strict, 105/105 tests, CLI smoke and package validation.

See `docs/releases/v0.4.0-evidence.md`.

## 0.4.0rc1 — 2026-10-25 — Incremental Reliability

### Added

- deterministic same-run replay of immutable Gmail History RAW pages;
- persisted History-event rehydration for crash recovery;
- durable account-scoped History event receipts for cross-run idempotence;
- independently versioned `history_receipt_schema_version = 1`;
- reuse of already persisted full-message RAW during event replay;
- `RUNNING`, `READY_TO_COMMIT`, `COMPLETED` and `RECOVERED_COMMIT` transaction evidence;
- candidate recovery without re-contacting Gmail;
- recovery when commit succeeded but final manifest persistence did not;
- compare-and-swap protection on checkpoint promotion and invalidation;
- automatic `Full Snapshot → bootstrap checkpoint → History catch-up` recovery;
- explicit rejection of PARTIAL snapshot recovery baselines;
- `--auto-full-resync/--no-auto-full-resync` CLI policy switch;
- crash/replay/CAS/recovery qualification scenarios.

### Recovery invariants

```text
frozen History page exists
→ reuse it, do not call Gmail for that page again

event already PROCESSED
→ do not repeat its effect

durable receipt already exists
→ mark replay event PROCESSED without repeating its effect

message RAW already exists
→ reuse it, do not hydrate again

candidate exists for same run
→ CAS-promote it without refetching Gmail History

committed checkpoint belongs to same run
→ recover final run state without refetching Gmail History

snapshot status != COMPLETED
→ never seed a new checkpoint

stale predecessor checkpoint
→ reject promotion/invalidation
```

### Preserved contracts

```text
normalized_schema_version      = 1
checkpoint_schema_version      = 1
history_receipt_schema_version = 1
OAuth scope                    = gmail.readonly
RAW message hydration          = format=full
PyIngestKit                    = 2.1.0
```

### Automated qualification

```text
candidate commit       f6eaba9ca52682163978e87db77fec20c277621b
GitHub Actions         40198698842
Python 3.11            PASS
Python 3.12            PASS
Python 3.13            PASS
Ruff                   PASS
mypy strict            PASS — 69 source files
pytest                 PASS — 99/99
CLI smoke              PASS
wheel + sdist          PASS
twine check            PASS
clean wheel install    PASS
```

Candidate distributions:

```text
gmail_ingestion-0.4.0rc1-py3-none-any.whl
sha256: 6f375ddb27b7359f39f2a9919a2382bfb71c9f88fa68544c9cb7f1d9f36f4795

gmail_ingestion-0.4.0rc1.tar.gz
sha256: c1e6467f4e65a0ca6e2f926b5fd638f821413e533ec4d534f85d89213f89d6b3
```

### Stable promotion gate

The rc1 evidence above is automated. It does **not** claim that the recovery path has already been
executed against a real Gmail mailbox. Stable `0.4.0` should perform a bounded live-mailbox
qualification before release.

## 0.4.0b2 — 2026-10-25 — Deletions + Label Mutations

### Added

- full mapping of Gmail `messagesDeleted[]`, `labelsAdded[]` and `labelsRemoved[]`;
- one elementary normalized event per message change or per message-label change;
- explicit `PENDING → PROCESSED` History event lifecycle;
- pure `sync/event_reducer.py` reducer;
- append-style message tombstone artifacts;
- explicit `UPSERT_ACTIVE` and `DEACTIVATE` message-label mutation artifacts;
- all four Gmail History types requested during incremental synchronization;
- exact duplicate event-ID suppression within one run;
- reducer metrics in the incremental manifest;
- regression tests for mixed additions/deletions/label changes and duplicates.

### Reducer contract

```text
MESSAGE_ADDED   → fetch + persist full message
MESSAGE_DELETED → message tombstone
LABEL_ADDED     → UPSERT_ACTIVE message-label mutation
LABEL_REMOVED   → DEACTIVATE message-label mutation
```

The generic Gmail `history.messages[]` collection remains ignored to avoid double-counting
changes already represented by the specific change-type fields.

### Transaction rule

```text
persist PENDING event
→ persist reducer effect
→ mark event PROCESSED
→ after every page/effect succeeds, create CANDIDATE
→ COMMIT
```

A reducer or hydration failure therefore leaves the previous committed checkpoint authoritative.

### Preserved contracts

```text
normalized_schema_version = 1
checkpoint_schema_version = 1
OAuth scope               = gmail.readonly
RAW message hydration     = format=full
0.3.0 filtered behavior   = unchanged
PyIngestKit               = 2.1.0
```

### Automated qualification

```text
commit              9448e3ea94e9d473ab3d4539722bcd6dce4fefe2
GitHub Actions      40195421766
Python 3.11         PASS
Python 3.12         PASS
Python 3.13         PASS
Ruff                PASS
mypy strict         PASS — 67 source files
pytest              PASS — 90/90
CLI smoke           PASS
wheel + sdist       PASS
twine check         PASS
clean wheel install PASS
```

Candidate distributions:

```text
gmail_ingestion-0.4.0b2-py3-none-any.whl
sha256: d281cae48abbb15d40d1769b58c02f384dfddccde294adc9ee4c88cc1ae7819e

gmail_ingestion-0.4.0b2.tar.gz
sha256: 0539b22cd6fe58c2ee0ac2926db2b73ae9a281b4f947be3f4cee1444b09cf125
```

### Deferred to 0.4.0rc1

- restart of the same partially written run;
- crash at page boundaries;
- crash after effect persistence but before processed marking;
- durable replay across separate runs;
- automatic Full Snapshot recovery after an expired History cursor;
- final incremental reliability qualification.

## 0.4.0b1 — 2026-10-25 — Gmail History Incremental

### Added

- `GmailHistoryApi` adapter for `users.history.list`;
- explicit `startHistoryId`, `pageToken`, `maxResults` and `historyTypes` handling;
- typed `GmailHistoryExpiredError` for Gmail HTTP 404 cursor expiry;
- `GmailHistoryEvent` and deterministic elementary event IDs;
- `MESSAGE_ADDED` mapping from `messagesAdded[]`;
- immutable RAW persistence for Gmail History pages;
- normalized per-event JSON persistence;
- full `messages.get(format=full)` hydration for affected messages;
- `IncrementalHistorySync` application use case;
- run-level incremental manifest with input/candidate/committed checkpoint evidence;
- `gmail-ingest incremental` CLI;
- typed `FullResyncRequiredError` after checkpoint invalidation;
- multi-page, no-change, hydration-failure, missing-checkpoint and expired-history tests.

### Transaction rule

```text
read COMMITTED
→ fetch all History pages
→ persist RAW history
→ persist MESSAGE_ADDED events
→ hydrate + persist changed messages
→ save CANDIDATE
→ COMMIT
```

Any failure before candidate promotion leaves the previous committed checkpoint authoritative.

### Preserved contracts

```text
normalized_schema_version = 1
checkpoint_schema_version = 1
OAuth scope               = gmail.readonly
RAW message hydration     = format=full
0.3.0 filtered behavior   = unchanged
```

### Deferred to 0.4.0b2 / rc1

- `MESSAGE_DELETED`;
- `LABEL_ADDED`;
- `LABEL_REMOVED`;
- tombstone and message-label mutations;
- automatic full-snapshot recovery after expired history;
- duplicate/crash/page-boundary reliability qualification.

## 0.4.0a1 — 2026-10-25 — Checkpoint Foundation

### Added

- immutable `GmailCheckpoint` domain model;
- explicit `CANDIDATE`, `COMMITTED` and `INVALID` checkpoint states;
- independently versioned `checkpoint_schema_version = 1`;
- `GmailCheckpointStore` / `CheckpointStore` persistence contract;
- atomic local filesystem checkpoint persistence;
- per-account checkpoint namespaces;
- run-scoped candidate checkpoints;
- explicit candidate promotion and discard operations;
- committed-checkpoint invalidation with a retained invalid record;
- regression tests proving candidate/committed separation and account isolation.

### Preserved contracts

```text
normalized_schema_version = 1
OAuth scope               = gmail.readonly
RAW hydration             = format=full
0.3.0 filtered behavior   = unchanged
```

This alpha intentionally does not call `users.history.list`. Gmail History pagination and
`messageAdded` begin in `0.4.0b1`.

## 0.3.0b1 — 2026-10-25 — Date Range / Filtered Sync

### Added

- typed `DateRange` and frozen resolved-range contracts;
- absolute `after` and `before` Gmail search bounds;
- relative `last N days` resolved once against the persistent run start timestamp;
- deterministic compilation of user query + date bounds into effective Gmail `q`;
- `RangeMessageSnapshot` library use case using the shared snapshot engine;
- `gmail-ingest range` CLI plus date options on `snapshot` for combined scopes;
- manifest evidence for requested dates, resolved dates and resolution timestamp;
- resume protection against changed absolute or relative scope;
- range/query/labels pagination and regression qualification.

### Preserved contracts

```text
normalized_schema_version = 1
RAW semantics              = unchanged
OAuth scope                = gmail.readonly
stateful checkpointing     = not introduced
```

`users.history.list`, committed checkpoints and Gmail History mutations remain reserved for
`0.4.0 — Incremental History Sync`.

## 0.3.0a1 — 2026-10-25 — Search Filters

### Added

- immutable `GmailFilter` contract for explicit Gmail discovery scope;
- Gmail-native `q` search support exposed through the library and CLI;
- repeatable `labelIds` filtering with deterministic canonicalization;
- explicit `includeSpamTrash` capture as part of effective scope;
- `FilteredMessageSnapshot` use case reusing the stable snapshot pipeline;
- requested and effective filter evidence in the manifest;
- deterministic SHA-256 scope fingerprint;
- resume protection against changed query/label/spam-trash scope;
- pagination tests proving filters remain applied across every discovery page.

### Compatibility contract

```text
normalized_schema_version = 1
RAW semantics              = unchanged
OAuth scope                = gmail.readonly
incremental checkpoints    = not introduced
```

Date ranges (`after`, `before`, `last N days`) remain deliberately reserved for
`0.3.0b1`.

## 0.2.0 — 2026-10-25 — Normalized Dataset Stable

### Added

- normalized schema contract v1 for `accounts`, `labels`, `messages`, `threads`,
  `message_headers`, `message_recipients`, `message_labels`, `mime_parts` and
  `attachments`;
- deterministic RAW-to-normalized transformation with no Gmail API calls in the normalization path;
- explicit grains, logical primary keys and stable typed fields;
- canonical multi-valued relations for headers, recipients and labels;
- thread aggregation derived entirely from normalized messages;
- stable Parquet candidate persistence with deterministic table ordering;
- schema-v1 and quality evidence artifacts;
- logical key, required-field, referential-integrity and attachment-quality checks;
- RAW fixture → normalize → Parquet → reload → logical equality qualification.

### Stable contract

```text
normalized_schema_version = 1
```

The stable milestone combines the planned `0.2.0a1` data model, `0.2.0b1` threads/relations,
and `0.2.0rc1` normalization qualification without changing the Gmail source-extraction semantics
already frozen in `0.1.0`.

### Automated qualification

The pre-promotion candidate passed on Python 3.11, 3.12 and 3.13:

```text
46/46 tests
Ruff
mypy strict
CLI smoke
wheel + sdist build
twine check
clean wheel install
PyIngestKit 2.1.0 verification
normalized_schema_version = 1
deterministic Parquet round-trip
```

## 0.1.0 — 2026-10-25 — Snapshot Foundation Stable

### Changed

- promote the qualified Snapshot Foundation contract without Gmail semantic drift;
- pin PyIngestKit from moving `main` to stable `v2.1.0`;
- add clean wheel/sdist build and SHA-256 release evidence;
- record Python/PyIngestKit compatibility and no-migration promotion notes;
- add a redacted real-mailbox qualification script;
- persist the exact Gmail `nextPageToken` for mid-discovery resume;
- bound hydration futures instead of submitting the entire mailbox at once;
- checkpoint discovery/fetch state in batches rather than after every hydrated message;
- add a bounded live qualification sample mode for large mailboxes;
- add deterministic `has:attachment` qualification for the binary path;
- bound physical attachment filenames with SHA-256-derived keys for filesystem portability;
- ignore local live-mailbox and qualification artifacts.

### Automated qualification

The qualified stable candidate `b08dde3220d583293dbd1eee96e35761f4194a98` is green on
Python 3.11, 3.12 and 3.13:

```text
43/43 tests
Ruff
mypy strict
CLI smoke
wheel + sdist build
twine check
clean wheel install
PyIngestKit 2.1.0 verification
SHA-256 generation
```

### Release gate

The package's own real Gmail Desktop OAuth E2E passed on 2026-10-25. The bounded qualification
processed 50 real messages, resumed the same run without count drift, and separately processed
10 `has:attachment` messages with 10 successful attachment downloads and zero attachment failures.

## 0.1.0rc1 — Snapshot Qualification

### Added

- automated snapshot-foundation qualification suite;
- end-to-end synthetic mailbox covering Profile, Labels, messages, MIME and attachments;
- OAuth refresh qualification through the real credential provider boundary;
- plain-text, HTML, multipart and non-UTF-8 body fixtures;
- shared-thread qualification;
- attachment retry, SHA-256 and separate binary-storage checks;
- empty-mailbox qualification;
- 1,001-message / three-page large-mailbox sample;
- secret-leak assertions across generated run artifacts;
- dedicated RC1 real-mailbox qualification runbook.

## 0.1.0b2 — MIME + Attachments + Manifest

- recursive MIME traversal;
- text/plain and text/html extraction;
- header and address parsing;
- attachment metadata and optional binary download;
- SHA-256, size limits and resumable binary persistence;
- manifest v1 and run metrics.

## 0.1.0b1 — Full Message Snapshot

- paginated message discovery;
- durable discovery queue;
- bounded concurrent message hydration;
- immutable RAW Gmail messages;
- retry and resumable snapshot execution.

## 0.1.0a2 — Labels

- labels.list / labels.get ingestion;
- immutable RAW label resources;
- labels candidate Parquet dataset.

## 0.1.0a1 — OAuth + Profile

- Desktop OAuth with gmail.readonly;
- private local credentials;
- transparent token refresh;
- users.getProfile mailbox identity.
