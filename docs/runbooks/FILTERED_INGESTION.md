# Filtered / Date Range Ingestion Runbook

## Scope

This runbook covers the `0.3.0` stateless discovery modes:

```text
query
labelIds
includeSpamTrash
after / before
last N days
combined filters
```

It does **not** cover Gmail History checkpoints or incremental synchronization.

## Search snapshot

```bash
gmail-ingest snapshot --query "has:attachment"
```

Multiple Gmail label IDs:

```bash
gmail-ingest snapshot \
  --label-id INBOX \
  --label-id IMPORTANT
```

## Absolute date range

```bash
gmail-ingest range \
  --after 2026-10-01 \
  --before 2026-10-24
```

The bounds are compiled to Gmail-native search syntax and recorded in the manifest.

## Relative date range

```bash
gmail-ingest range --last-days 7
```

`last_days` is resolved exactly once against the persistent run start timestamp. Resuming the same
`run_id` reuses the same resolved lower bound and scope fingerprint.

## Combined filter

```bash
gmail-ingest range \
  --query "has:attachment from:example.com" \
  --label-id IMPORTANT \
  --after 2026-10-01 \
  --before 2026-10-24
```

## Manifest evidence

Each run records:

```text
scope.kind
scope.fingerprint
requested_filter
effective_filter
```

For a relative range, `effective_filter` also records the frozen resolution timestamp.

## Resume rule

Resume is allowed only when the original run scope is preserved. Changing query, labels,
Spam/Trash policy, absolute bounds or `last_days` rejects the resume.

## Real Gmail qualification

From an authenticated development environment:

```bash
python scripts/qualify_live_filtered_ingestion.py \
  --query "has:attachment" \
  --label-id INBOX \
  --last-days 30 \
  --sample-size 10 \
  --require-query-hit
```

The script writes redacted evidence only. Gmail message bodies, subjects, addresses and OAuth
secrets are not included in the evidence JSON.
