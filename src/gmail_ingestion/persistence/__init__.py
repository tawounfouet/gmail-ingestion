"""Gmail persistence adapters."""

from .attachment_binary import AttachmentBinaryWriter, ExistingAttachmentBinary
from .checkpoint_store import (
    CheckpointConflictError,
    CheckpointNotFoundError,
    CheckpointStore,
    CheckpointStoreError,
    GmailCheckpointStore,
    LocalCheckpointStore,
)
from .history_receipt_store import (
    HISTORY_RECEIPT_SCHEMA_VERSION,
    GmailHistoryReceiptStore,
    HistoryEventReceipt,
    LocalHistoryReceiptStore,
)
from .history_writer import HistoryEventWriter, IncrementalManifestWriter, RawHistoryWriter
from .incremental_mutation_writer import IncrementalMutationWriter
from .labels_writer import LabelsParquetWriter, LabelsWriteResult
from .message_analysis import MessageAnalysisWriter
from .parquet import NormalizedParquetWriter, NormalizedParquetWriteResult
from .projection_store import (
    LocalProjectionStore,
    ProjectionConflictError,
    ProjectionNotFoundError,
    ProjectionStoreError,
)
from .raw_labels import RawLabelsWriter
from .raw_message_writer import RawMessageWriter

__all__ = [
    "AttachmentBinaryWriter",
    "CheckpointConflictError",
    "CheckpointNotFoundError",
    "CheckpointStore",
    "CheckpointStoreError",
    "ExistingAttachmentBinary",
    "GmailCheckpointStore",
    "GmailHistoryReceiptStore",
    "HISTORY_RECEIPT_SCHEMA_VERSION",
    "HistoryEventReceipt",
    "HistoryEventWriter",
    "IncrementalManifestWriter",
    "IncrementalMutationWriter",
    "LabelsParquetWriter",
    "LabelsWriteResult",
    "LocalCheckpointStore",
    "LocalHistoryReceiptStore",
    "MessageAnalysisWriter",
    "NormalizedParquetWriteResult",
    "NormalizedParquetWriter",
    "ProjectionConflictError",
    "ProjectionNotFoundError",
    "ProjectionStoreError",
    "LocalProjectionStore",
    "RawHistoryWriter",
    "RawLabelsWriter",
    "RawMessageWriter",
]
