# RC8 - Current source prequalification, memory-bounded path

**Software:** `0.5.0rc8` candidate. **Stable 0.5.0: NO-GO** until independent Gmail evidence, exact-main CI and explicit gate approval in issue #29.

This runbook supersedes the eager materializer for live mailbox data.
**Do not re-run** `scripts/materialize_rc8_current_candidate.py`. It failed twice due to
`AVAILABLE_RAM_BELOW_RESERVE`. The partitioned implementation is new and
must pass synthetic CI and operator preflights before live execution.

## Immutable evidence and identity

- Private scoped source: `181109` message IDs, History `15908705`, hash
  `f7f1ab628b62b6f8c98633ddf547a986aa6619eb4e3c5566816da0078e02d903`.
- Baseline run: `c394dcbb-f5da-4ae0-abb8-c9e6dcf81479`,
  177546 messages, 711 digest-verified segments.
- Six committed History runs, frontier `15908919`.
- Recovery run: `5d22b716-7bf2-4490-b5d0-00541427931e` with 48
  independent full RAW, all SHA-256 validated; **not** historical event-local RAW.
- Source plan: `rc8_aa053e4ff4938a1a56f2130180d64455` with 70 original RAW
  gaps, 48 current-present, 13 historically unproven scope-absent and 9
  later committed deletions. **Do not synthesize missing events.**

## 1. Freeze and preflight

From the Mac with local artifacts, once the implementation PR has passed
synthetic tests and been merged:

```bash
git switch main
git pull --ff-only

GMAIL_PY="/opt/homebrew/opt/python@3.14/bin/python3.14"
PRIV="$HOME/.local/share/gmail-ingestion-private"
SCOPED="$(ls -1t "$PRIV"/rc8-scoped-*.json | head -n 1)"
OUT_ROOT="$PWD/qualification/rc8-current-candidates"

test -s "$SCOPED" || exit 2
mkdir -p "$OUT_ROOT"
"$GMAIL_PY" - "$SCOPED" "$OUT_ROOT" <<'PY'
import json
import sys
from pathlib import Path
import psutil
import shutil

source = json.loads(Path(sys.argv[1]).read_text())
root = Path(sys.argv[2])
assert source["account_id"] == "acct_84757009c00c40cf859cb796"
assert source["history_end"] == "15908705"
assert source["message_count"] == 181109
assert source["include_spam_trash"] is False
assert source["id_set_sha256"] == (
    "f7f1ab628b62b6f8c98633ddf547a986aa6619eb4e3c5566816da0078e02d903"
)
disk = shutil.disk_usage(root).free / 1024**3
ram = psutil.virtual_memory().available / 1024**3
print(f"RAM available: {ram:.2f} GiB")
print(f"Disk free: {disk:.2f} GiB")
assert disk >= 60 and ram >= 32, "STOP, insufficient live headroom"
assert not any(root.rglob("*.sqlite3")), "STOP, inspect retained scratch files"
assert not any(root.rglob("*.parquet")), "STOP, inspect candidate files"
print("RC8 ISOLATED PREFLIGHT PASS")
PY
```

No Gmail call or full data read occurs in this preflight.

## 2. Build a source-scoped, isolated Current

**Run once** if step 1 and exact-main CI passed. The script reads seven
baseline tables in verified Arrow batches, uses a private disk-backed
SQLite message reducer, writes streaming Parquet row groups and independently
replays the same frozen baseline/deltas in a separate scratch database.

```bash
caffeinate -i env PYTHONPATH="$PWD/src" \
  "$GMAIL_PY" scripts/materialize_rc8_partitioned_candidate.py \
  --artifact-root .gmail-ingestion-live-0.5.0 \
  --projection-root "$OUT_ROOT" \
  --account-id acct_84757009c00c40cf859cb796 \
  --baseline-run-id c394dcbb-f5da-4ae0-abb8-c9e6dcf81479 \
  --delta-run-id ab677d4e-be1e-40f0-80c7-5edcf5abc6fa \
  --delta-run-id 73153907-c8fa-46e6-884a-55bfe67ccb71 \
  --delta-run-id 807ad9f4-66a1-40e5-aadf-f18aefe850fc \
  --delta-run-id 8b133cc6-7ebe-4b3a-b26c-d309a058ca4c \
  --delta-run-id fb6bb5cf-525a-430d-990d-ba2f5d66900d \
  --delta-run-id ef78f381-0c0e-4559-99da-129917ac0746 \
  --source-inventory "$SCOPED" \
  --recovery-run-id 5d22b716-7bf2-4490-b5d0-00541427931e \
  --min-free-gib 40 \
  --min-available-ram-gib 24 \
  --batch-size 2048
```

Expect a `projection_id`, all seven `row_counts`,
`deterministic_replay_equal=true`, `source_message_count=181109`, and
`status=CANDIDATE_DATASET_NOT_SOURCE_QUALIFIED`.

On any failure: **stop**, retain Parquet and SQLite scratch as forensic
private data, inspect available RAM/disk, do not delete/retry with the same
root or lower limits. The script does not promote `current.json`, change
checkpoint or write to Gmail. Private messages are not logged.

## 3. Audit all Current rows offline

Set `PROJECTION_ID` to the exact output from step 2.

```bash
env PYTHONPATH="$PWD/src" "$GMAIL_PY" \
  scripts/audit_rc8_partitioned_candidate.py \
  --projection-root "$OUT_ROOT" \
  --account-id acct_84757009c00c40cf859cb796 \
  --projection-id "$PROJECTION_ID" \
  --source-inventory "$SCOPED" \
  --batch-size 2048
```

Requires physical hashes for seven Parquet tables, full row counts,
complete source message ID parity, zero orphan child rows, thread membership
and count parity, MIME/attachment graph references, full canonical logical
digest equality and independent replay digest evidence.

This proves **offline structural parity**, not live Gmail label or MIME
equivalence. Do not call it stable GO.

## 4. Verify sample against live Gmail, close race window

Gmail API read-only; choose the frozen `SCOPED`, same `PROJECTION_ID`.

```bash
caffeinate -i env PYTHONPATH="$PWD/src" \
  "$GMAIL_PY" scripts/verify_rc8_source_parity.py \
  --account-id acct_84757009c00c40cf859cb796 \
  --artifact-root .gmail-ingestion-live-0.5.0 \
  --projection-root "$OUT_ROOT" \
  --baseline-run-id c394dcbb-f5da-4ae0-abb8-c9e6dcf81479 \
  --projection-id "$PROJECTION_ID" \
  --source-inventory "$SCOPED" \
  --sample-size 32
```

**If source drift is detected:** stop. It cannot be fixed by skipping the
message or forging an event. Re-observe scoped IDs/History, commit new
incremental catchup, replan and recapture only current missing RAW with a
new versioned candidate identity. Do not change the old source evidence.

**A 32-message PASS does not prove all Gmail labels/threads/MIME.**
Before stable, the entire qualification scope must be compared to Gmail.
A separate **explicitly opt-in exhaustive verifier** performs a sorted
merge join over all candidate message tables and calls Gmail FULL once for
each source ID without retaining message payloads. It checks message
fields, labels, thread references and MIME/attachments for every ID, then
replays History from the frozen frontier for the complete source cohort.

**This is expensive** (for the current source: 181109 Gmail FULL GET
requests plus History pages), may encounter rate limits or an expired
History cursor and provides no automatic resume if interrupted. Run only
after the 32-message smoke PASS, API quota assessment and deliberate
operator authorization. Failure means NO-GO, not permission to skip IDs.

```bash
caffeinate -i env PYTHONPATH="$PWD/src" \
  "$GMAIL_PY" scripts/verify_rc8_full_source_parity.py \
  --account-id acct_84757009c00c40cf859cb796 \
  --artifact-root .gmail-ingestion-live-0.5.0 \
  --projection-root "$OUT_ROOT" \
  --baseline-run-id c394dcbb-f5da-4ae0-abb8-c9e6dcf81479 \
  --projection-id "$PROJECTION_ID" \
  --source-inventory "$SCOPED" \
  --max-messages 181109 \
  --batch-size 2048 \
  --max-history-pages 4000 \
  --allow-exhaustive-full-reads
```

Expected only on full real-source convergence:
`status=EXHAUSTIVE_SOURCE_PARITY_PASS_RELEASE_REVIEW_REQUIRED`,
`full_messages_verified=181109` and
`source_ids_labels_threads_mime_verified=true`. This still does not
self-approve stable promotion. A failed or partially completed scan
emits no certificate and should not trigger automatic retry or deletion.


## 5. Remaining gates

- [ ] Bounded live build (resource evidence, correct manifest, all seven tables).
- [ ] Exhaustive offline audit and independent deterministic replay PASS.
- [ ] Independent Gmail source scope, labels/threads/MIME/attachments and
      full reconciliation at closed History frontier; immutable exceptions.
- [ ] Both relevant event classes and release reliability gates on live data.
- [ ] PR and exact-main RC CI, security checks, privacy/security review.
- [ ] Redacted evidence report and explicit GO/NO-GO in issue #29.

**No stable tag, no wheel/sdist publication, no Current promotion** from
this runbook. Stable v0.5.0 remains NO-GO until all conditions pass.
