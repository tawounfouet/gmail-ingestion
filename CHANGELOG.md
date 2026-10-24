# Changelog

All notable changes to Gmail Ingestion are documented in this file.

The project follows Semantic Versioning with pre-release milestones during the pre-1.0 phase.

## 0.1.0 — Unreleased stable promotion

### Changed

- promote the qualified Snapshot Foundation contract without Gmail semantic drift;
- pin PyIngestKit from moving `main` to stable `v2.1.0`;
- add clean wheel/sdist build and SHA-256 release evidence;
- record Python/PyIngestKit compatibility and no-migration promotion notes;
- add a redacted real-mailbox qualification script.

### Automated qualification

The release branch is green on Python 3.11, 3.12 and 3.13. The exact stable branch candidate
`800647747f8efeb48290e536f72cdc319f57e034` passed:

```text
37/37 tests
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

The remaining gate is the package's own real Gmail Desktop OAuth E2E. Until it passes, merge,
`v0.1.0` tag and GitHub stable release remain intentionally blocked.

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
