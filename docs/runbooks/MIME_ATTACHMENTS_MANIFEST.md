# Gmail MIME + Attachments + Manifest — 0.1.0b2

This runbook qualifies the `GMAIL_04` milestone on a dedicated Gmail test mailbox.

## Scope

The milestone covers:

```text
headers
recursive MIME tree
nested multipart
text/plain
text/html
charset handling
attachment metadata
inline resources
optional binary download
SHA-256
binary size limits
manifest v1
run metrics
partial attachment failures
```

It does **not** freeze the analytical Parquet schema. Stable normalized tables belong to `0.2.0`.

## Preconditions

```bash
gmail-ingest auth
gmail-ingest profile
```

Use a dedicated mailbox containing at least:

1. one plain-text email;
2. one HTML-only email;
3. one multipart/alternative email;
4. one nested multipart/mixed email;
5. one normal attachment;
6. one inline/CID resource;
7. one message with non-UTF-8 text when practical.

## Metadata-only qualification

Run:

```bash
gmail-ingest snapshot
```

Expected:

```text
manifest_status: COMPLETED
attachments_detected: >= 1
attachments_fetched: 0
bytes_downloaded: 0
```

Verify:

```text
runs/gmail/messages/<run_id>/manifest.json
runs/gmail/messages/<run_id>/candidate/mime/*.json
```

Each MIME artifact must expose:

```text
headers
normalized_headers
addresses
text_body
html_body
mime_parts
attachments
warnings
```

Attachment rows must use:

```text
download_status = METADATA_ONLY
```

## Binary download qualification

Run a fresh snapshot:

```bash
gmail-ingest snapshot --download-attachments
```

Verify binaries are separate from message/MIME JSON:

```text
runs/gmail/attachments/<run_id>/raw/
```

For each downloaded attachment, verify:

```text
download_status = DOWNLOADED
content_hash_sha256 != null
storage_uri != null
```

and verify the SHA-256 against the stored bytes independently.

## Size-limit qualification

Run with a deliberately low ceiling:

```bash
gmail-ingest snapshot \
  --download-attachments \
  --max-attachment-bytes 1024
```

A larger attachment must become:

```text
download_status = SKIPPED_SIZE_LIMIT
```

The manifest must contain a warning and the run may remain `COMPLETED`.

## Partial failure qualification

Simulate or provoke one attachment retrieval failure while other messages remain readable.

Expected:

```text
manifest_status = PARTIAL
attachments_failed >= 1
errors[].scope = attachment
```

The already successful message RAW and MIME artifacts must remain usable.

## Resume qualification

Take the `run_id` of a binary-download run and execute:

```bash
gmail-ingest snapshot \
  --run-id <RUN_UUID> \
  --download-attachments
```

Expected:

- completed discovery is not repeated;
- immutable message RAW is not duplicated;
- existing attachment binaries are not redownloaded;
- the original manifest `started_at` is retained;
- final counts remain coherent.

Changing settings for the same run must fail explicitly. For example, a run started with
`--download-attachments` must not be resumed in metadata-only mode.

## Manifest acceptance

The final `manifest.json` must contain at least:

```text
schema_version
connector_name
connector_version
run_id
account_id
started_at
completed_at
status
mode
filters
message_format
include_spam_trash
download_attachments
input_checkpoint
candidate_checkpoint
committed_checkpoint
messages_discovered
messages_fetched
messages_failed
attachments_fetched
bytes_downloaded
dataset_version
errors
warnings
storage_paths
```

## Exit criteria

Do not tag `v0.1.0b2` until the real-mailbox checks prove:

```text
[ ] nested multipart traversed correctly
[ ] text/plain extracted
[ ] text/html extracted
[ ] unknown/duplicate headers retained in RAW
[ ] duplicate header names represented in technical analysis
[ ] mailbox addresses parsed
[ ] charset handling verified
[ ] attachment metadata detected
[ ] inline resource distinguished from normal attachment
[ ] metadata-only mode performs no binary attachment calls
[ ] binary download writes separate immutable artifacts
[ ] SHA-256 verified
[ ] size limit verified
[ ] partial attachment error visible in manifest
[ ] resume does not redownload existing binaries
[ ] final manifest is JSON and metrics reconcile
[ ] no OAuth secret appears in artifacts
```

After this passes, proceed to `0.1.0rc1 — Snapshot Qualification`.
