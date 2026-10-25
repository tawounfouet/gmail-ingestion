"""Data-quality checks for Gmail normalized datasets."""

from .normalized import NormalizedQualityError, NormalizedQualityReport, validate_normalized_tables

__all__ = [
    "NormalizedQualityError",
    "NormalizedQualityReport",
    "validate_normalized_tables",
]
