# Source-ID parity after first independent Gmail inventory (RC8)

## Evidence captured by the operator (2026-11-06)

- The read-only Gmail observation completed successfully with 363
  `users.messages.list` pages and 181,200 unique message IDs.
- Inventory `history_start == history_end == 15904124`: no History changes
  were returned during the listing interval.
- Inventory explicitly used `include_spam_trash=true`.
- Previous local checkpoint: `15903183`. Gmail history IDs are not consecutive
  event counters. An increment of 941 does **not** imply 941 message changes.
- 58 event-level missing RAW remain unresolved by subsequent committed deletes.
- The operator reported only 8.6 GiB remaining free storage.

No message IDs or mail content are stored in the repository. The inventory
file remains under the operator's private home directory.

## Scope prerequisite: inspect the original full-snapshot manifest

The `ReliableIncrementalSync` bootstrap invokes `FullMessageSnapshot`
without `include_spam_trash`, whose default is **false**. The independent
inventory explicitly includes both. Therefore, an apparent ID mismatch would
not necessarily mean that the History reducer lost events.

Check the *actual* original manifest, not merely the default:

```bash
python - <<'PY'
import json
from pathlib import Path

run_id = "c394dcbb-f5da-4ae0-abb8-c9e6dcf81479"
manifest = (
    Path(".gmail-ingestion-live-0.5.0")
    / "runs" / "gmail" / "messages" / run_id / "manifest.json"
)
data = json.loads(manifest.read_text(encoding="utf-8"))
print({
    "status": data.get("status"),
    "mode": data.get("mode"),
    "include_spam_trash": data.get("include_spam_trash"),
    "messages_discovered": data.get("messages_discovered"),
    "messages_fetched": data.get("messages_fetched"),
    "message_limit": data.get("message_limit"),
    "filters": data.get("filters"),
})
PY
```

Share only that sanitized summary, not the underlying raw manifest.

If `include_spam_trash=false`, **do not claim mailbox-wide ID parity**
against the 181,200-ID full-scope source inventory. This is a historical
coverage gap requiring an explicitly versioned source-reconciliation layer
or a compatible baseline. It is not permission to delete or rewrite the
177,546-message baseline.

## Additional read-only parity script

`scripts/compare_source_inventory_ids.py` reconstructs message-ID
membership by streaming only the validated `message_id` columns of the
711 baseline segments and replaying committed message additions/deletions.
It does **not** construct Current projection tables, decode MIME, download
mail content, or advance a checkpoint. It refuses incompatible source scopes.

It also requires the supplied committed History chain to cover the
inventory's `history_end` frontier. If the committed checkpoint is still
`15903183`, the comparison refuses to proceed because the observed Gmail
frontier was `15904124`. Do not reinterpret the observed inventory as though
it described the earlier checkpoint.

**Do not start a new full snapshot or projection to make this pass.**

After inspecting the baseline scope and confirming disk headroom, a new
bounded `gmail-ingest incremental --no-auto-full-resync` can advance the
existing committed cursor. Record its new run ID. Do not confuse the
resulting checkpoint with the older source observation.

The parity CLI accepts all committed delta IDs in chain order, including
the newer run. It filters typed events after the source observation to
compare membership specifically at the observed frontier. It returns
aggregate counts and digest values only.

Example (replace the final UUID with the actual new run ID):

```bash
GMAIL_PY="$(head -n 1 "$(command -v gmail-ingest)" | sed 's/^#!//')"
INVENTORY="$(ls -1t "$HOME"/.local/share/gmail-ingestion-private/source-*.json | head -n 1)"

"$GMAIL_PY" scripts/compare_source_inventory_ids.py \
  --artifact-root .gmail-ingestion-live-0.5.0 \
  --account-id acct_84757009c00c40cf859cb796 \
  --baseline-run-id c394dcbb-f5da-4ae0-abb8-c9e6dcf81479 \
  --delta-run-id ab677d4e-be1e-40f0-80c7-5edcf5abc6fa \
  --delta-run-id 73153907-c8fa-46e6-884a-55bfe67ccb71 \
  --delta-run-id 807ad9f4-66a1-40e5-aadf-f18aefe850fc \
  --delta-run-id 8b133cc6-7ebe-4b3a-b26c-d309a058ca4c \
  --delta-run-id NEW_COMMITTED_RUN_UUID \
  --source-inventory "$INVENTORY"
```

A `PASS_ID_MEMBERSHIP_ONLY` proves **message-ID membership at the
observed frontier only** under the required matching scope. It never
qualifies labels, threads, MIME, attachment content, original historical
RAW or stable release readiness. A failed or incomplete check is not
automatically evidence of loss.

## Stable release gate remains closed

Remaining acceptance evidence:
- resolve scope mismatch or reconcile the missing source cohort with
  explicit provenance;
- classify each remaining missing event-local RAW and its current relevance;
- demonstrate actual Current candidate IDs, labels, and threads match a
  properly aligned Gmail observation;
- independent rebuild equality, resource limits, qualified immutable
  candidate and exact-main CI;
- explicit stable promotion decision.

Until those conditions are satisfied, `v0.5.0` is **NO-GO**.
