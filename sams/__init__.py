"""Safer Agent Memory Standard (SAMS) reference implementation.

    from sams import SQLiteBackend, AgentSession, DreamerSession, Owner
    from sams import PostgresBackend      # needs the [postgres] extra

See STANDARD.md for the normative rules this package enforces.
"""

from .errors import AMSError, NotFound, PermissionDenied, ReviewMismatch, ScopeError, VersionConflict
from .harness import AgentSession, DreamerSession, Owner, review_digest
from .redact import redact, review_flags
from .sqlite_store import SQLiteBackend

__version__ = "0.1.1"


def __getattr__(name: str):
    # Imported lazily so the core package stays stdlib-only.
    if name == "PostgresBackend":
        from .postgres_store import PostgresBackend
        return PostgresBackend
    raise AttributeError(f"module 'sams' has no attribute {name!r}")


__all__ = [
    "AMSError", "NotFound", "PermissionDenied", "ReviewMismatch", "ScopeError", "VersionConflict",
    "AgentSession", "DreamerSession", "Owner", "SQLiteBackend", "redact", "review_digest", "review_flags",
]
