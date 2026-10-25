"""Gmail persistence adapters."""

from .attachment_binary import AttachmentBinaryWriter, ExistingAttachmentBinary
from .labels_writer import LabelsParquetWriter, LabelsWriteResult
from .message_analysis import MessageAnalysisWriter
from .parquet import NormalizedParquetWriteResult, NormalizedParquetWriter
from .raw_labels import RawLabelsWriter
from .raw_message_writer import RawMessageWriter

__all__ = [
    "AttachmentBinaryWriter",
    "ExistingAttachmentBinary",
    "LabelsParquetWriter",
    "LabelsWriteResult",
    "MessageAnalysisWriter",
    "NormalizedParquetWriteResult",
    "NormalizedParquetWriter",
    "RawLabelsWriter",
    "RawMessageWriter",
]
