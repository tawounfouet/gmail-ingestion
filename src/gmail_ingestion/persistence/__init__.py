"""Gmail persistence adapters."""

from .labels_writer import LabelsParquetWriter, LabelsWriteResult
from .raw_labels import RawLabelsWriter
from .raw_message_writer import RawMessageWriter

__all__ = [
    "LabelsParquetWriter",
    "LabelsWriteResult",
    "RawLabelsWriter",
    "RawMessageWriter",
]
