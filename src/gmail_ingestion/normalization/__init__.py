"""Gmail normalization layer."""

from .address_parser import ParsedAddress, normalize_address, parse_addresses
from .header_parser import HeaderEntry, HeaderParseError, ParsedHeaders, parse_headers
from .label_normalizer import LABEL_FIELDS, GmailLabelNormalizationError, normalize_labels
from .mime_walker import MimeAnalysis, MimeParseError, MimePartRecord, analyze_message

__all__ = [
    "GmailLabelNormalizationError",
    "HeaderEntry",
    "HeaderParseError",
    "LABEL_FIELDS",
    "MimeAnalysis",
    "MimeParseError",
    "MimePartRecord",
    "ParsedAddress",
    "ParsedHeaders",
    "analyze_message",
    "normalize_address",
    "normalize_labels",
    "parse_addresses",
    "parse_headers",
]
