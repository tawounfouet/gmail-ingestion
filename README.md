# Gmail Ingestion

Reliable, versioned Gmail ingestion built on [PyIngestKit](https://github.com/tawounfouet/pyingestkit).

> **Current milestone:** `GMAIL_00 — 0.0.1 Repository Bootstrap`

The project turns a Gmail mailbox into a controlled dataset pipeline rather than a one-off email export.

```text
Gmail API
   ↓
Discovery / Extraction
   ↓
RAW Preservation
   ↓
Normalization
   ↓
Validation
   ↓
Dataset Versioning
   ↓
Publication
   ↓
Reconciliation
```

## Status

`0.0.1` establishes the engineering foundation only. Gmail OAuth and real API access begin in
`0.1.0a1 — OAuth + Profile`.

## Requirements

- Python 3.11+
- Git

## Bootstrap

```bash
git clone https://github.com/tawounfouet/gmail-ingestion.git
cd gmail-ingestion
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

Check the CLI:

```bash
gmail-ingest --help
gmail-ingest version
python -m gmail_ingestion --help
```

Run the quality gate:

```bash
ruff check .
mypy src
pytest
```

## Repository layout

```text
gmail-ingestion/
├── pyproject.toml
├── README.md
├── LICENSE
├── .gitignore
├── .env.example
├── src/
│   └── gmail_ingestion/
├── tests/
├── docs/
│   └── specs/
├── examples/
└── .github/
    └── workflows/
        └── ci.yml
```

## Security baseline

The connector is read-only by design for its initial Gmail capability. Secrets and OAuth tokens must
never be committed. The repository ignores `.env`, `credentials.json`, `client_secret*.json`,
`token*.json`, `tokens/`, and `secrets/` by default.

## Roadmap

```text
0.0.1      Repository Bootstrap
0.1.0a1    OAuth + Profile
0.1.0a2    Labels
0.1.0b1    Full Message Snapshot
0.1.0b2    MIME + Attachments + Manifest
0.2.0      Normalized Dataset
0.3.0      Search / Date Range / Filtered Ingestion
0.4.0      Incremental History Sync
0.5.0      Push / CDC
0.6.0      Reconciliation & Reliability
0.7.0      Dataset Versioning + Object Storage
0.8.0      Communication Analytics Gold
0.9.0      Multi-account & Public Contracts
1.0.0      Production Qualified
```

See [`docs/specs/`](docs/specs/) for the design corpus and implementation roadmap.

## License

MIT
