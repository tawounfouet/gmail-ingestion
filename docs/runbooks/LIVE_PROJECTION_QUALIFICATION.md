# Live Projection Qualification — v0.5.0 Stable Gate

## Purpose

This runbook qualifies **Gmail Ingestion 0.5.0 — Materialized Current State** against bounded
artifacts captured from a real Gmail mailbox.

Projection itself remains read-only and makes **zero Gmail API calls**. Mailbox mutations required
to exercise Gmail History are performed manually in Gmail and only on a disposable qualification
message.

## Safety rules

- use a dedicated/test mailbox when possible;
- use a disposable message created specifically for this qualification;
- never permanently delete a non-disposable message;
- do not commit `credentials.json`, OAuth tokens or local qualification roots;
- keep the connector on `gmail.readonly`;
- if an expected History event is missing, capture another bounded disposable scenario instead of
  fabricating evidence.

## Candidate

Expected package line:

```text
0.5.0rc3
```

or stable promotion:

```text
0.5.0
```

Verify:

```bash
gmail-ingest version
```

## Dedicated local roots

Use qualification-specific roots so the test does not overwrite another local Current projection:

```bash
export ARTIFACT_ROOT=.gmail-ingestion-v050-live
export CONTROL_ROOT=.gmail-ingestion-v050-live-control
export PROJECTION_ROOT=.gmail-ingestion-v050-live-projection
```

## Step 1 — establish a real normalized baseline

Start from a fresh qualification control root and run:

```bash
gmail-ingest incremental \
  --artifact-root "$ARTIFACT_ROOT" \
  --checkpoint-root "$CONTROL_ROOT"
```

With no committed checkpoint, the reliable incremental path performs:

```text
Full Snapshot
→ normalized baseline
→ bootstrap checkpoint
→ History catch-up
→ committed incremental run
```

Record both values printed by the command:

```text
full_snapshot_run_id: <BASELINE_RUN_ID>
run_id:               <DELTA_RUN_0>
```

`DELTA_RUN_0` is part of the complete baseline-to-target History chain even when it contains no
business event.

## Step 2 — create a disposable Gmail message

Using Gmail UI or an external sender, create one disposable message in the qualification mailbox.

Do **not** use the connector to mutate Gmail.

Capture the addition:

```bash
gmail-ingest incremental \
  --artifact-root "$ARTIFACT_ROOT" \
  --checkpoint-root "$CONTROL_ROOT"
```

Record:

```text
run_id: <DELTA_RUN_1>
```

The accumulated evidence must eventually include:

```text
MESSAGE_ADDED >= 1
```

## Step 3 — add a disposable label

In Gmail UI, add a qualification-only label to the disposable message, for example:

```text
GMAIL_INGESTION_V050_QUAL
```

Capture:

```bash
gmail-ingest incremental \
  --artifact-root "$ARTIFACT_ROOT" \
  --checkpoint-root "$CONTROL_ROOT"
```

Record:

```text
run_id: <DELTA_RUN_2>
```

Expected accumulated evidence:

```text
LABEL_ADDED >= 1
```

## Step 4 — remove the label

Remove the same qualification label in Gmail UI, then capture:

```bash
gmail-ingest incremental \
  --artifact-root "$ARTIFACT_ROOT" \
  --checkpoint-root "$CONTROL_ROOT"
```

Record:

```text
run_id: <DELTA_RUN_3>
```

Expected:

```text
LABEL_REMOVED >= 1
```

## Step 5 — permanently delete only the disposable message

Move the disposable message to Trash and permanently delete that disposable message from Gmail.

Then capture:

```bash
gmail-ingest incremental \
  --artifact-root "$ARTIFACT_ROOT" \
  --checkpoint-root "$CONTROL_ROOT"
```

Record:

```text
run_id: <DELTA_RUN_4>
```

Expected:

```text
MESSAGE_DELETED >= 1
```

## Step 6 — run the stable projection gate

Supply the incremental run IDs in exact checkpoint order, starting with the catch-up run created
immediately after the baseline:

```bash
python scripts/qualify_live_projection.py \
  --account-id <ACCOUNT_ID> \
  --baseline-run-id <BASELINE_RUN_ID> \
  --delta-run-id <DELTA_RUN_0> \
  --delta-run-id <DELTA_RUN_1> \
  --delta-run-id <DELTA_RUN_2> \
  --delta-run-id <DELTA_RUN_3> \
  --delta-run-id <DELTA_RUN_4> \
  --artifact-root "$ARTIFACT_ROOT" \
  --projection-root "$PROJECTION_ROOT" \
  --evidence-out v0.5.0-live-projection-evidence.json
```

The gate fails closed if:

- any run is not `COMPLETED` or `RECOVERED_COMMIT`;
- persisted event evidence is incomplete;
- the checkpoint chain contains a gap;
- one of the four History event classes is absent;
- Current materialization fails;
- incremental projection and full rebuild produce different logical digests;
- atomic Current promotion is missing;
- replay does not recognize the already-current frontier.

## Required PASS evidence

Expected console summary:

```text
Projection live-artifact qualification: PASS
...
MESSAGE_ADDED:   >= 1
MESSAGE_DELETED: >= 1
LABEL_ADDED:     >= 1
LABEL_REMOVED:   >= 1
```

Evidence JSON must include:

```text
status                         PASS
all_four_event_classes         true
incremental_equals_rebuild     true
atomic_current_pointer         true
immutable_version_evidence     true
replay_already_current         true
gmail_api_calls_by_projection  0
oauth_scope_change             false
contains_message_content       false
contains_oauth_secrets         false
```

## Stable promotion rule

Do not promote `0.5.0rc3` to `0.5.0` until:

```text
automated CI                 PASS
exact-main RC3 CI            PASS
real Gmail bounded capture   PASS
live projection gate         PASS
redacted evidence reviewed   PASS
```

The stable promotion must then remain version/release-metadata/documentation only. Any change to
projection semantics requires another release candidate.
