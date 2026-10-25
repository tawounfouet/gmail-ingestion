# Changelog

All notable changes to Gmail Ingestion are documented in this file.

The project follows Semantic Versioning with pre-release milestones during the pre-1.0 phase.

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
