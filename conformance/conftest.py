"""Backend adapter for the conformance suite.

To test your own backend, add a factory to BACKENDS that returns a fresh,
empty backend implementing sams.backend.Backend. Every test runs against
every factory listed here.
"""

from __future__ import annotations

import itertools

import pytest

from sams import AgentSession, DreamerSession, Owner, SQLiteBackend


def sqlite_backend(tmp_path):
    return SQLiteBackend(tmp_path / "memory")


BACKENDS = {
    "sqlite": sqlite_backend,
    # "postgres": postgres_backend,   # v0.2
}

_ids = itertools.count(1)


@pytest.fixture(params=list(BACKENDS))
def backend(request, tmp_path):
    return BACKENDS[request.param](tmp_path)


@pytest.fixture
def owner(backend):
    return Owner(backend, name="owner")


@pytest.fixture
def make_session(backend):
    def make(assigned: str = "project:demo", agent: str = "agent") -> AgentSession:
        return AgentSession(backend, session_id=f"s{next(_ids)}", agent=agent, assigned=assigned)
    return make


@pytest.fixture
def make_dreamer(backend):
    def make(scope: str = "project:demo") -> DreamerSession:
        return DreamerSession(backend, job_id=f"d{next(_ids)}", scope=scope)
    return make
