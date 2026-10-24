import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pyarrow.parquet as pq
from pyingestkit.artifacts.filesystem import LocalArtifactStore

from gmail_ingestion.normalization import normalize_labels
from gmail_ingestion.persistence import LabelsParquetWriter, RawLabelsWriter


def test_labels_raw_and_parquet_outputs(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    run_id = uuid4()
    list_payload = {
        "labels": [{"id": "INBOX", "name": "INBOX", "type": "system"}]
    }
    detail = {
        "id": "INBOX",
        "name": "INBOX",
        "type": "system",
        "messagesTotal": 5,
    }

    raw = RawLabelsWriter(store).write(run_id, list_payload, [detail])
    assert len(raw) == 2
    assert json.loads(Path(raw[0].path).read_text()) == list_payload

    dataset = normalize_labels(
        "acct_0123456789abcdef01234567",
        [detail],
        ingested_at=datetime(2026, 10, 24, tzinfo=UTC),
    )
    written = LabelsParquetWriter(store).write(run_id, dataset)
    table = pq.read_table(written.parquet_path)

    assert table.num_rows == 1
    assert table.column("label_id").to_pylist() == ["INBOX"]
    assert written.schema_path.is_file()
