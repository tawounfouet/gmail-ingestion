# Gmail Ingestion 0.1.0 — Migration

## From 0.1.0rc1

No data migration is required.

`0.1.0` is a promotion of the qualified Snapshot Foundation contract. Existing local credential
namespaces and run artifacts remain compatible.

## Dependency change

The only intentional release-hardening dependency change is:

```text
before: pyingestkit @ ...@main
after:  pyingestkit @ ...@v2.1.0
```

This removes moving-branch installation drift.

## Credentials

Do not copy, commit or reserialize OAuth tokens during the upgrade.

Existing credential stores remain under the same private per-account namespace.

## Run artifacts

Existing 0.1.0rc1 RAW, MIME and manifest artifacts do not need rewriting.

A new run produced by 0.1.0 records `connector_version = "0.1.0"` in its manifest.
