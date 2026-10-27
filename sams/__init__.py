"""Safer Agent Memory Standard (SAMS) reference implementation.

    from sams import SQLiteBackend, AgentSession, DreamerSession, Owner

See STANDARD.md for the normative rules this package enforces.
"""

from .errors import AMSError, NotFound, PermissionDenied, ReviewMismatch, ScopeError, VersionConflict
from .harness import AgentSession, DreamerSession, Owner, review_digest
from .redact import redact, review_flags
from .sqlite_store import SQLiteBackend

__version__ = "0.1.1"

__all__ = [
    "AMSError", "NotFound", "PermissionDenied", "ReviewMismatch", "ScopeError", "VersionConflict",
    "AgentSession", "DreamerSession", "Owner", "SQLiteBackend", "redact", "review_digest", "review_flags",
]
