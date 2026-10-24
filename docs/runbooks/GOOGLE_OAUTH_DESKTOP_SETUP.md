# Google OAuth Desktop Setup — Gmail Ingestion

This runbook prepares a local developer mailbox for milestone `0.1.0a1`.

## 1. Create or select a Google Cloud project

Use a Google Cloud project dedicated to the connector or to local development.

## 2. Enable the Gmail API

Enable the Gmail API for the selected project.

## 3. Configure the Google Auth consent screen

Configure the application branding, audience and contact information required by Google. For an
external application still under development, add the mailbox used for integration testing as a
permitted test user when Google requires it.

## 4. Create OAuth credentials

Create an OAuth 2.0 client with application type **Desktop app** and download the JSON file.

For the default CLI flow, save it locally as:

```text
credentials.json
```

The repository `.gitignore` excludes this file.

## 5. Install Gmail Ingestion

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## 6. Authorize the mailbox

```bash
gmail-ingest auth
```

The connector requests exactly:

```text
https://www.googleapis.com/auth/gmail.readonly
```

A browser consent flow is started. The callback uses a temporary local port selected by the OS.

For a terminal where a browser must not be opened automatically:

```bash
gmail-ingest auth --no-browser
```

## 7. Verify the profile

```bash
gmail-ingest profile
```

Expected fields:

```text
account_id
email_address
messages_total
threads_total
history_id
```

The command calls Gmail `users.getProfile` for `userId="me"`.

## 8. Verify token reuse

Run the profile command again:

```bash
gmail-ingest profile
```

No new consent should be required while stored credentials remain reusable. If the access token has
expired and a refresh token is available, the connector refreshes it and atomically rewrites the
private token file.

## 9. Local credential location

Default:

```text
~/.local/share/gmail-ingestion/credentials/
```

Override it explicitly when needed:

```bash
gmail-ingest auth --credential-dir /secure/private/path
gmail-ingest profile --credential-dir /secure/private/path
```

Never upload `token.json`, `credentials.json`, or the credential directory to GitHub.

## 10. Acceptance evidence for 0.1.0a1

Before tagging the milestone, verify on a dedicated test mailbox:

```text
[ ] OAuth consent succeeds
[ ] only gmail.readonly is requested
[ ] users.getProfile succeeds
[ ] an opaque account_id is created
[ ] a second profile call reuses stored credentials
[ ] an expired access token can be refreshed
[ ] no OAuth token appears in terminal output or logs
```
