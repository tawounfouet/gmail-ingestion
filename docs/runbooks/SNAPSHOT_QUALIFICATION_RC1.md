# Snapshot Qualification — 0.1.0rc1

This runbook is the release gate between the implemented Snapshot Foundation and `0.1.0` stable.

## Automated qualification

CI must pass on Python 3.11, 3.12 and 3.13:

```bash
ruff check .
mypy src
pytest
```

The RC1 qualification suite covers:

```text
OAuth token refresh
Profile
SYSTEM + USER labels
plain text
HTML
multipart/alternative
shared thread IDs
non-UTF-8 charset
message retry
attachment retry
attachment SHA-256
separate binary storage
manifest integrity
empty mailbox
1,001-message sample
3 discovery pages
secret non-leakage
```

## Real-mailbox gate

Use a dedicated Gmail test mailbox. Do not paste OAuth client secrets or token JSON into tickets,
chat, logs, screenshots, or test fixtures.

Install the candidate:

```bash
python -m pip install -e ".[dev]"
gmail-ingest version
```

Expected:

```text
0.1.0rc1
```

### 1. OAuth + refresh

```bash
gmail-ingest auth
gmail-ingest profile
gmail-ingest profile
```

Acceptance:

- consent succeeds with `gmail.readonly` only;
- the second profile call reuses stored credentials;
- no access token or refresh token appears in terminal output.

### 2. Labels

```bash
gmail-ingest labels
```

Acceptance:

- at least the expected SYSTEM labels are present;
- any test USER label is present;
- RAW labels and candidate Parquet/schema artifacts exist.

### 3. Metadata-only snapshot

Prepare representative test messages:

- plain text;
- HTML only;
- multipart/alternative;
- two messages in one thread;
- one normal attachment;
- one inline/CID resource when possible.

Run:

```bash
gmail-ingest snapshot
```

Acceptance:

- status `COMPLETED`;
- RAW message count matches the discovered/fetched count;
- MIME artifacts exist for every fetched message;
- attachment metadata is present;
- `attachments_fetched = 0`;
- manifest counts reconcile.

### 4. Binary attachment snapshot

Run a fresh snapshot:

```bash
gmail-ingest snapshot --download-attachments
```

Acceptance:

- binaries are stored under the separate `gmail.attachments` run namespace;
- SHA-256 values match the stored bytes;
- `attachments_fetched` and `bytes_downloaded` reconcile.

### 5. Resume

Reuse the emitted run ID with the same options:

```bash
gmail-ingest snapshot \
  --run-id <RUN_UUID> \
  --download-attachments
```

Acceptance:

- no rediscovery when discovery was complete;
- no duplicate immutable RAW;
- no redownload of existing attachment binaries;
- original `started_at` is preserved.

### 6. Large mailbox sample

Run against the dedicated mailbox or a representative test mailbox large enough to require
multiple `messages.list` pages.

Acceptance:

- every page is traversed;
- discovered IDs are unique;
- fetched count equals expected scope count;
- manifest counts reconcile;
- no missing RAW artifacts.

## Release decision

Promote to `0.1.0` only when all items are checked:

```text
[ ] automated CI matrix green
[ ] OAuth real-mailbox check passed
[ ] Profile real-mailbox check passed
[ ] Labels real-mailbox check passed
[ ] metadata-only snapshot passed
[ ] attachment binary snapshot passed
[ ] resume passed
[ ] multi-page mailbox sample passed
[ ] no critical bug known
[ ] README current
[ ] examples current
[ ] CHANGELOG current
```

If any critical item fails, keep `0.1.0rc1` in HOLD and correct it before stable release.
