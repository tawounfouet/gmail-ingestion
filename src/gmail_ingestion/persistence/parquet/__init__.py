"""Parquet persistence for stable normalized Gmail tables."""

from .normalized_writer import NormalizedParquetWriter, NormalizedParquetWriteResult

__all__ = ["NormalizedParquetWriteResult", "NormalizedParquetWriter"]
