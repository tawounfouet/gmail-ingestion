"""Persistent control state for resumable Gmail ingestion."""

from .message_discovery import MessageDiscoveryError, MessageDiscoveryQueue

__all__ = ["MessageDiscoveryError", "MessageDiscoveryQueue"]
