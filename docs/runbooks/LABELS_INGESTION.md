# Gmail Labels Ingestion — 0.1.0a2

This runbook exercises the first Gmail business dataset.

## Preconditions

The OAuth + Profile capability must already have created an active local account:

```bash
gmail-ingest auth
gmail-ingest profile
```

## Run the labels ingestion

```bash
gmail-ingest labels
```

The default flow:

1. loads reusable credentials;
2. verifies the mailbox identity with `users.getProfile`;
3. calls `users.labels.list`;
4. calls `users.labels.get` for each label to enrich counters and colors;
5. writes immutable RAW JSON through PyIngestKit's `ArtifactStore`;
6. normalizes the stable `labels` schema;
7. writes `candidate/labels.parquet`;
8. writes `candidate/labels.schema.json`.

Artifacts are grouped under one PyIngestKit `run_id`.

## List-only mode

Google's `users.labels.list` response contains only the summary label fields. To avoid one
`labels.get` request per label:

```bash
gmail-ingest labels --no-details
```

Detailed counters and colors are nullable in the normalized schema and remain null in this mode.

## Custom artifact root

```bash
gmail-ingest labels --artifact-root /secure/data/gmail-ingestion
```

The default is `.gmail-ingestion/`, which is ignored by Git.

## Acceptance evidence

Before tagging `v0.1.0a2`, verify with a real dedicated Gmail mailbox:

```text
[ ] users.labels.list succeeds
[ ] users.labels.get enrichment succeeds
[ ] system and user labels are both preserved
[ ] no system label list is hard-coded
[ ] RAW list response exists
[ ] RAW detailed label responses exist
[ ] labels.parquet matches the 14-column target schema
[ ] (account_id, label_id) is unique
[ ] no OAuth token appears in artifacts or terminal output
```
