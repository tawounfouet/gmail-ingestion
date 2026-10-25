# Gmail Ingestion 0.3.0 — Migration from 0.2.0

## Summary

No normalized-data migration is required.

```text
0.2.0 normalized_schema_version = 1
0.3.0 normalized_schema_version = 1
```

Existing full-snapshot callers continue to work without supplying any filter.

## New optional library parameters

`FullMessageSnapshot.run()` accepts optional discovery scope values:

```text
query
label_ids
include_spam_trash
after
before
last_days
```

Existing calls remain valid.

## New application use cases

```python
from gmail_ingestion.application import FilteredMessageSnapshot, RangeMessageSnapshot
from gmail_ingestion.domain import DateRange, GmailFilter
```

## New CLI options

```text
gmail-ingest snapshot --query ...
gmail-ingest snapshot --label-id ...
gmail-ingest snapshot --after ...
gmail-ingest snapshot --before ...
gmail-ingest snapshot --last-days ...

gmail-ingest range ...
```

## Resume behavior

A run created under an explicit filter must be resumed with the same requested scope. A changed
scope is rejected instead of silently mixing discovery populations.

## Relative date behavior

`--last-days N` is resolved once from the original run start. Resuming the same run does not slide
the time window forward.

## Rollback

Rollback to `v0.2.0` requires no normalized dataset conversion because schema v1 is unchanged.
Runs created with `0.3.0` filter metadata should not be resumed by `0.2.0`.
