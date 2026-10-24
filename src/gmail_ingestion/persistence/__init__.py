"""Gmail persistence adapters."""

from .labels_writer import LabelsParquetWriter, LabelsWriteResult
from .raw_labels import RawLabelsWriter

__all__ = ["LabelsParquetWriter", "LabelsWriteResult", "RawLabelsWriter"]
