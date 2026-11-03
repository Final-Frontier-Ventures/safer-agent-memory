"""Backend adapter for the conformance suite.

To test your own backend, add a factory to BACKENDS that returns a fresh,
empty backend implementing sams.backend.Backend (or yields one, then cleans
up). Every test runs against every factory listed here.

The Postgres backend runs when SAMS_TEST_POSTGRES_DSN names a database where
that login may create schemas and roles, for example
postgresql://postgres:postgres@localhost:5432/postgres. Each test gets its own
schema and a non-superuser owner login, so row-level security is in force.
"""

from __future__ import annotations

import inspect
import itertools
import os
import secrets

import pytest

from sams import AgentSession, DreamerSession, Owner, SQLiteBackend


def sqlite_backend(tmp_path):
    return SQLiteBackend(tmp_path / "memory")


def postgres_backend(tmp_path):
    dsn = os.environ.get("SAMS_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("set SAMS_TEST_POSTGRES_DSN to run the Postgres backend")
    import psycopg
    from psycopg.conninfo import make_conninfo
    from sams.postgres_store import PostgresBackend, grant, install_schema

    schema = f"t_{secrets.token_hex(6)}"
    role, password = f"{schema}_owner", secrets.token_hex(16)
    install_schema(dsn, schema)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(f'CREATE ROLE "{role}" LOGIN PASSWORD \'{password}\'')
    grant(dsn, role, "owner", "*", schema)
    try:
        yield PostgresBackend(make_conninfo(dsn, user=role, password=password), schema=schema)
    finally:
        with psycopg.connect(dsn, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
            conn.execute(f'DROP ROLE "{role}"')


BACKENDS = {
    "sqlite": sqlite_backend,
    "postgres": postgres_backend,
}

_ids = itertools.count(1)


@pytest.fixture(params=list(BACKENDS))
def backend(request, tmp_path):
    made = BACKENDS[request.param](tmp_path)
    if inspect.isgenerator(made):
        yield next(made)
        made.close()
    else:
        yield made


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
