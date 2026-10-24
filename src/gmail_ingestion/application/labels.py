"""Ingest the Gmail labels catalogue as RAW plus normalized Parquet."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from pyingestkit.artifacts.base import ArtifactStore
from pyingestkit.dataset import Dataset

from gmail_ingestion.api import GmailApiClient, map_gmail_profile
from gmail_ingestion.auth.provider import GmailOAuthProvider
from gmail_ingestion.normalization import normalize_labels
from gmail_ingestion.persistence import LabelsParquetWriter, RawLabelsWriter

ClientFactory = Callable[..., GmailApiClient]


@dataclass(frozen=True, slots=True)
class LabelsIngestionResult:
    """Evidence produced by one labels ingestion run."""

    run_id: UUID
    account_id: str
    label_count: int
    raw_artifact_count: int
    dataset: Dataset
    parquet_path: Path
    schema_path: Path


class IngestLabels:
    """Execute the GMAIL_02 labels vertical slice."""

    def __init__(
        self,
        provider: GmailOAuthProvider,
        artifact_store: ArtifactStore,
        *,
        client_factory: ClientFactory = GmailApiClient,
    ) -> None:
        self.provider = provider
        self.artifact_store = artifact_store
        self.client_factory = client_factory

    def run(
        self,
        account_id: str | None = None,
        *,
        include_details: bool = True,
        run_id: UUID | None = None,
    ) -> LabelsIngestionResult:
        resolved, credentials = self.provider.load(account_id)
        client = self.client_factory(credentials=credentials)

        profile = map_gmail_profile(resolved, client.get_profile())
        self.provider.store.verify_account_email(resolved, profile.email_address)

        list_payload = client.labels.list_labels()
        summaries = _extract_label_payloads(list_payload)
        normalized_payloads = (
            [client.labels.get_label(_label_id(payload)) for payload in summaries]
            if include_details
            else summaries
        )

        actual_run_id = run_id or uuid4()
        raw_artifacts = RawLabelsWriter(self.artifact_store).write(
            actual_run_id,
            list_payload,
            normalized_payloads if include_details else (),
        )
        dataset = normalize_labels(resolved, normalized_payloads)
        written = LabelsParquetWriter(self.artifact_store).write(actual_run_id, dataset)

        return LabelsIngestionResult(
            run_id=actual_run_id,
            account_id=resolved,
            label_count=len(dataset),
            raw_artifact_count=len(raw_artifacts),
            dataset=dataset,
            parquet_path=written.parquet_path,
            schema_path=written.schema_path,
        )


def _extract_label_payloads(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    labels = payload.get("labels", [])
    if not isinstance(labels, list):
        raise ValueError("labels.list payload has an invalid labels field")
    normalized: list[Mapping[str, Any]] = []
    for label in labels:
        if not isinstance(label, Mapping):
            raise ValueError("labels.list contains a non-object label")
        normalized.append(label)
    return normalized


def _label_id(payload: Mapping[str, Any]) -> str:
    value = payload.get("id")
    if not isinstance(value, str) or not value:
        raise ValueError("Gmail label is missing id")
    return value
