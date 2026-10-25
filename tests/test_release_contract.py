from importlib.metadata import requires, version

import gmail_ingestion
from gmail_ingestion.config import GMAIL_READONLY_SCOPE
from gmail_ingestion.schemas import NORMALIZED_SCHEMA_VERSION


def test_filtered_ingestion_stable_preserves_stable_contracts() -> None:
    assert gmail_ingestion.__version__ == "0.3.0"
    assert NORMALIZED_SCHEMA_VERSION == "1"
    assert GMAIL_READONLY_SCOPE == "https://www.googleapis.com/auth/gmail.readonly"
    assert version("pyingestkit") == "2.1.0"

    requirements = requires("gmail-ingestion") or []
    assert any(
        "pyingestkit @ git+https://github.com/tawounfouet/pyingestkit.git@v2.1.0" in item
        for item in requirements
    )
