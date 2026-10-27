"""Exceptions raised by SAMS. Every refusal is an exception, never a silent no-op."""


class AMSError(Exception):
    """Base class for all SAMS errors."""


class PermissionDenied(AMSError):
    """The caller's role or scope does not allow this operation (C1, C7, C8)."""


class ScopeError(AMSError):
    """The scope string is malformed or outside the caller's assignment."""


class VersionConflict(AMSError):
    """The entry changed since the caller read it (optimistic locking, C5).

    Re-read the entry, redraft against the new content, and retry.
    """

    def __init__(self, scope: str, path: str, expected: int, actual: int):
        self.scope, self.path, self.expected, self.actual = scope, path, expected, actual
        super().__init__(
            f"{scope}/{path}: expected version {expected}, found {actual}; re-read and redraft"
        )


class NotFound(AMSError):
    """No entry, history version or proposal matches."""
