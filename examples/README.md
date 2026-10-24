# Examples

The Snapshot Foundation candidate is exercised through the CLI.

## 1. Authorize a Gmail test mailbox

```bash
gmail-ingest auth
gmail-ingest profile
```

## 2. Ingest labels

```bash
gmail-ingest labels
```

## 3. Create the safe default snapshot

```bash
gmail-ingest snapshot
```

This keeps attachments in metadata-only mode.

## 4. Create a snapshot with binary attachments

```bash
gmail-ingest snapshot \
  --download-attachments \
  --max-attachment-bytes 10485760
```

## 5. Resume an existing snapshot

```bash
gmail-ingest snapshot \
  --run-id <RUN_UUID> \
  --download-attachments \
  --max-attachment-bytes 10485760
```

Resume with exactly the same snapshot settings used by the original run.

## 6. Inspect the manifest

```bash
cat .gmail-ingestion/runs/gmail/messages/<RUN_UUID>/manifest.json
```

The candidate is qualified automatically by `tests/qualification/test_snapshot_rc1.py`.
Real Gmail validation is documented in
[`docs/runbooks/SNAPSHOT_QUALIFICATION_RC1.md`](../docs/runbooks/SNAPSHOT_QUALIFICATION_RC1.md).
