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
from .labels_writer import LabelsParquetWriter, LabelsWriteResult
from .message_analysis import MessageAnalysisWriter
from .parquet import NormalizedParquetWriter, NormalizedParquetWriteResult
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
    "LabelsParquetWriter",
    "LocalCheckpointStore",
    "LabelsWriteResult",
    "MessageAnalysisWriter",
    "NormalizedParquetWriteResult",
    "NormalizedParquetWriter",
    "RawLabelsWriter",
    "RawMessageWriter",
]
