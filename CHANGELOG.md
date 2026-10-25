# Changelog

All notable changes to Gmail Ingestion are documented in this file.

The project follows Semantic Versioning with pre-release milestones during the pre-1.0 phase.

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

The stable release is approved for merge and publication after post-merge CI.

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

### Qualification status

Automated CI qualification is green on Python 3.11, 3.12 and 3.13: 36/36 tests pass and mypy strict
reports no issues across 46 source files.

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
