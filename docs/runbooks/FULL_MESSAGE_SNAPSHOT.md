# Gmail Full Message Snapshot — 0.1.0b1

This runbook qualifies the first complete message acquisition milestone.

## Preconditions

Authorize a dedicated Gmail mailbox first:

```bash
gmail-ingest auth
gmail-ingest profile
```

## Start a new snapshot

```bash
gmail-ingest snapshot
```

The command:

1. verifies the authenticated mailbox with `users.getProfile`;
2. captures `H_START = historyId`;
3. traverses every `users.messages.list` page;
4. persists a deduplicated `message_discovery` control queue before hydration;
5. hydrates every discovered message with `users.messages.get(format=full)`;
6. retries safe GET failures with a bounded PyIngestKit `RetryPolicy`;
7. limits concurrent hydrations to a bounded worker pool;
8. persists every hydrated message as immutable RAW JSON through PyIngestKit;
9. updates discovery status after every completed message.

Default controls follow the project requirements:

```text
include_spam_trash = false
max_results        = 500
max_workers        = 4
max_attempts       = 5
```

## Resume a partial snapshot

Keep the emitted `run_id` and rerun:

```bash
gmail-ingest snapshot --run-id <RUN_UUID>
```

A resumed run reloads `control/message_discovery.json`, skips messages already confirmed
`FETCHED`, detects immutable RAW written immediately before a crash, and retries only unresolved
messages. Completed discovery is not repeated.

## Include Spam and Trash

```bash
gmail-ingest snapshot --include-spam-trash
```

## Acceptance evidence

Do not tag `v0.1.0b1` until a dedicated mailbox proves:

```text
[ ] every messages.list page is traversed
[ ] discovered IDs are persisted before hydration
[ ] duplicate discovery does not duplicate queue rows
[ ] every resolvable message is hydrated with format=full
[ ] every hydrated message has immutable RAW JSON
[ ] transient GET failure is retried
[ ] permanent per-message failure remains persisted as FAILED
[ ] restart with the same run_id does not duplicate RAW
[ ] restart resumes only unresolved messages
[ ] discovered/fetched/failed counts reconcile
[ ] no OAuth token is written to RAW or control artifacts
```
