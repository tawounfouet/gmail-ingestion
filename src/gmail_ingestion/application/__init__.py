"""Application use cases."""

from .authenticate import AuthenticateMailbox
from .profile import GetMailboxProfile

__all__ = ["AuthenticateMailbox", "GetMailboxProfile"]
