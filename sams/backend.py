"""Storage backend contract.

A backend stores memory entries and proposals. It does not decide who may do
what: the harness (`sams.harness`) enforces roles, scopes, taint and status.
Backends SHOULD add their own enforcement as a second layer (database roles,
row-level security, read-only files), so a bug in the harness is not enough to
break the rules.

Every entry lives in a *container*: the file-level scope that owns it ('org',
'project:<id>' or 'tenant:<id>'). Run scratchpads live inside the container of
the session's assigned scope, so a tenant's run notes never sit outside that
tenant's storage.

To add a backend (for example Postgres), implement this protocol and wire it
into `conformance/conftest.py`.
"""

from __future__ import annotations

from typing import Any, Iterable, Protocol

Entry = dict[str, Any]
"""Keys: scope, path, status, pending_key, description, content, version,
author, source_session, tainted, base_version, updated_at."""

Proposal = dict[str, Any]
"""Keys: id, scope, path, description, content, diff, base_version, evidence, occurrences,
rationale, flags, created_by, created_at, decision, decided_by, decided_at, note."""


class Backend(Protocol):
    # ---- entries -------------------------------------------------------
    def get(self, container: str, scope: str, path: str, status: str = "active",
            pending_key: str = "") -> Entry | None: ...

    def list(self, container: str, scope: str | None = None,
             statuses: Iterable[str] = ("active",)) -> list[Entry]: ...

    def search(self, container: str, scope: str, query: str,
               statuses: Iterable[str] = ("active",), limit: int = 20) -> list[Entry]: ...

    def write(self, container: str, scope: str, path: str, *, status: str, pending_key: str,
              content: str, description: str, author: str, source_session: str | None,
              tainted: bool, expected_version: int, base_version: int = 0) -> Entry:
        """Conditional upsert. `expected_version` is the version of the row being
        written as the caller last read it (0 = must not exist). Raises
        VersionConflict on mismatch. The previous row goes to history."""
        ...

    def delete(self, container: str, scope: str, path: str, status: str,
               pending_key: str = "") -> None: ...

    def history(self, container: str, scope: str, path: str) -> list[Entry]: ...

    def clear_runs(self, container: str) -> int: ...

    def run_scopes(self, container: str) -> list[str]: ...

    # ---- proposals -----------------------------------------------------
    def add_proposal(self, container: str, proposal: Proposal) -> int: ...

    def list_proposals(self, container: str, undecided_only: bool = True) -> list[Proposal]: ...

    def get_proposal(self, container: str, proposal_id: int) -> Proposal | None: ...

    def decide_proposal(self, container: str, proposal_id: int, decision: str,
                        decided_by: str, note: str = "") -> None: ...
