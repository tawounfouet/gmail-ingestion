# 0.5.0 - Current reconciliation / release-readiness preflight

## Purpose and status

This document records the post-RC7 decision and the **offline preflight** that follows
successful persistence of the post-target Gmail History interval. It is not a
certificate of historical fidelity or an authorization to publish Current.

**Decision: historical target NO-GO, stable 0.5.0 NO-GO until live source validation.**

## User-provided operational evidence (2026-11-06)

The operator supplied the terminal output of a committed incremental run:

| Field | Value |
|---|---|
| Run | `8b133cc6-7ebe-4b3a-b26c-d309a058ca4c` |
| Start History checkpoint | `15694719` |
| Committed History checkpoint | `15903183` |
| Status | `COMPLETED` |
| Pages | 37 |
| Persisted events | 8,202 |
| Hydrated messages | 3,044 |
| Unavailable messages | 8 |
| Tombstones | 60 |
| Label mutations | 5,090 |
| Auto full snapshot recovery | false |
| Durable receipts | true |

These are **operator-reported local values**, not data downloaded into CI or
re-verified from the Mac through GitHub. The script below validates the local
manifest, the history chain, and provenance when executed with the artifacts.

Previously committed historical evidence:
- baseline: `c394dcbb-f5da-4ae0-abb8-c9e6dcf81479`;
- deltas: `ab677d4e-be1e-40f0-80c7-5edcf5abc6fa`,
  `73153907-c8fa-46e6-884a-55bfe67ccb71`,
  `807ad9f4-66a1-40e5-aadf-f18aefe850fc`;
- dated recovery captures:
  `adef5962-aa39-49ab-88a3-34ec1567edd6`,
  `44a872e6-e0a9-4d6d-83e8-5df74570e3b4`.

## Safety

- Maintain the 711 baseline segments, all historical deltas, both recovery
  captures, and the post-target run as immutable evidence.
- Never synthesize a Gmail delete, label mutation, missing RAW, or historic
  `historyId`.
- Never use a dated `gmail.recovery_evidence` response as original event-local RAW.
- Keep OAuth `gmail.readonly`; never expose source message IDs or private content
  in a published qualification report.
- Verify free disk before expensive candidate builds. The operator reported
  only 9.9 GiB available before the catchup. This is not adequate proof of
  sufficient remaining headroom for a full projection.
- Do not run `gmail-ingest project --promote` on this dataset to "make it pass".

## Step 1 - Offline source-provenance preflight (read-only)

From the repository root on the Mac with the local artifacts:

```bash
git checkout main
git pull --ff-only
df -h .

python scripts/audit_current_release_readiness.py \
  --artifact-root .gmail-ingestion-live-0.5.0 \
  --account-id acct_84757009c00c40cf859cb796 \
  --baseline-run-id c394dcbb-f5da-4ae0-abb8-c9e6dcf81479 \
  --delta-run-id ab677d4e-be1e-40f0-80c7-5edcf5abc6fa \
  --delta-run-id 73153907-c8fa-46e6-884a-55bfe67ccb71 \
  --delta-run-id 807ad9f4-66a1-40e5-aadf-f18aefe850fc \
  --catchup-run-id 8b133cc6-7ebe-4b3a-b26c-d309a058ca4c \
  --first-recovery-run-id adef5962-aa39-49ab-88a3-34ec1567edd6 \
  --second-recovery-run-id 44a872e6-e0a9-4d6d-83e8-5df74570e3b4 \
  --expected-checkpoint 15903183
```

The command performs **no Gmail API calls or persistent writes**. It fails
closed if:
- baseline identity or completion status is wrong;
- any delta is incomplete or out of checkpoint order;
- catchup input/output checkpoints differ from the expected frontiers;
- recovery manifests disagree on the 49 captured / 3 404 cohort;
- a capture manifest refers to missing immutable evidence.

It classifies missing event-local RAW across the complete chain. A later
committed deletion is a valid basis to classify a now-irrelevant add, but an
unmatched 404 is never converted into a tombstone.

Its result is intentionally `NO_GO_SOURCE_VALIDATION_PENDING` even when all
local manifests are consistent. **Artifact-only parity cannot certify a current
snapshot against an independently observed Gmail mailbox.**

## Step 2 - Engineer a distinct Current reconciliation candidate

This is additional engineering work, not functionality completed by the
preflight script. The implementation must:

1. Carry the provenance policy into the projection identity and digest.
2. Consume dated recovery evidence only via an explicit opt-in policy.
3. Store a typed exception ledger, including the historic 52 and the
   8 unavailable messages found by the new run, deduplicated by message ID.
4. Keep all historical runs immutable and the published Current pointer untouched.
5. Fail closed for unknown or unclassified exceptions.
6. Maintain bounded resource usage and enough disk space to write/validate
   a candidate, without assuming that 96 GiB installed RAM is available RAM.

## Step 3 - Independent live-source consistency gate

Use `gmail.readonly` with a bounded source inventory strategy that can
enumerate Gmail IDs and labels and establish a source frontier. Compare:
- message presence and absence with the local candidate;
- labels for sampled or comprehensive message IDs as required by the gate;
- thread references and derived membership;
- attachment / MIME reference consistency.

Close the observation race using committed `history.list` changes after the
inventory start frontier. Run a deterministic rebuild and compare digests.
Record coverage and differences, keeping message content and raw IDs out of
shared evidence. If the source frontier expires, start a separately approved
rebuild; never pretend that the inventory is point-in-time consistent.

## Step 4 - Release GO/NO-GO

Before changing package version, tagging, or publishing:
- [ ] Real-source reconciliation is implemented and tested.
- [ ] A complete post-inventory History catchup is durable.
- [ ] Unresolved exceptions are correctly classified or a new explicitly
      scoped snapshot is created.
- [ ] Candidate data passes reference and digest checks.
- [ ] The candidate uses its own identity and cannot overwrite strict history.
- [ ] Memory/disk safety is observed.
- [ ] PR matrix and exact-main CI pass at the final RC source SHA.
- [ ] Redacted real-mailbox evidence is reviewed and accepted.
- [ ] A recorded release decision authorizes stable promotion.

Until then: **DO NOT create `v0.5.0` or publish a stable GitHub Release.**


## Optional source-ID observation (read-only; NOT a stable gate)

A separate bounded source discovery utility now exists. It retrieves Gmail
message ID references, not message bodies, across the entire mailbox
(including Spam and Trash), then applies the post-listing History delta to
produce an observed ID set. This is an **independent observation** only;
it does not alter the ingestion checkpoint or substitute for real-time
label/thread verification, a second inventory, or a qualified candidate.

Save the private file **outside the Git repository**; it contains Gmail IDs.
Do not publish, upload to GitHub, or paste its full JSON output into a PR.

```bash
mkdir -p "$HOME/.local/share/gmail-ingestion-private"
chmod 700 "$HOME/.local/share/gmail-ingestion-private"

python scripts/observe_current_source_inventory.py \
  --account-id acct_84757009c00c40cf859cb796 \
  --evidence-out "$HOME/.local/share/gmail-ingestion-private/source-inventory-20261106.json" \
  --max-message-pages 1000 \
  --max-history-pages 200 \
  --page-size 500
```

The output file is create-once with mode `0600`; an existing file causes
a failure rather than silent replacement. A successful read-only capture
prints *aggregate counts and a digest only*. The capture may involve hundreds
of `messages.list` calls and History pages for a large mailbox. Confirm
network/quota tolerance and the source scope first.

If either pagination cap is reached or History expires, no qualified source
inventory is emitted. A complete single-pass inventory can still miss IDs
under concurrent pagination shifts. A second independently converged
inventory, content-free ID set comparison, labels, and thread checks are
required before claiming equality against the projected state.
