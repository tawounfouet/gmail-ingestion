# Gmail Ingestion 0.2.0 — Migration

## From 0.1.0

The 0.1.0 RAW Snapshot Foundation remains valid and unchanged.

0.2.0 adds a new downstream normalized contract:

```text
RAW
  ↓
normalized_schema_version = 1
```

No OAuth credential migration, checkpoint migration or RAW rewrite is required.

## Existing RAW artifacts

Existing Gmail message JSON from 0.1.0 can be loaded and supplied to the normalization API. The
normalizer does not call Gmail and therefore does not require source re-download solely to build the
normalized model.

There is no automatic historical backfill CLI in 0.2.0; callers explicitly select the RAW payloads
they want to normalize.

## Existing label Parquet

The label candidate schema introduced before 0.2.0 remains compatible with the stable v1 labels
contract. 0.2.0 centralizes the schema contract and quality rules around the complete normalized
dataset.

## Breaking-change policy

A future change to a stable table grain, logical key, table name, or incompatible field type must be
treated as a migration rather than a silent rewrite.
