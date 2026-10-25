"""Gmail normalization layer."""

from .address_parser import ParsedAddress, normalize_address, parse_addresses
from .dataset_normalizer import GmailNormalizationError, NormalizedDataset, normalize_snapshot
from .header_parser import HeaderEntry, HeaderParseError, ParsedHeaders, parse_headers
from .label_normalizer import LABEL_FIELDS, GmailLabelNormalizationError, normalize_labels
from .mime_walker import MimeAnalysis, MimeParseError, MimePartRecord, analyze_message

__all__ = [
    "GmailLabelNormalizationError",
    "GmailNormalizationError",
    "HeaderEntry",
    "HeaderParseError",
    "LABEL_FIELDS",
    "MimeAnalysis",
    "MimeParseError",
    "MimePartRecord",
    "NormalizedDataset",
    "ParsedAddress",
    "ParsedHeaders",
    "analyze_message",
    "normalize_address",
    "normalize_labels",
    "normalize_snapshot",
    "parse_addresses",
    "parse_headers",
]
