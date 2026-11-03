"""Postgres backend with row-level security (STANDARD.md section 6).

One schema holds every container. The container ('org', 'project:<id>',
'tenant:<id>') is a column, and row-level security decides which rows each
database login can see and change. The binding lives in the database, not in
anything the client sends:

    sams_grants(role_name, kind, container)

    kind      container            the login may
    agent     project:x / tenant:x read org and that container; insert/update
                                   only `pending` rows, or `active` rows of run
                                   scopes, in that container; never org
    dreamer   one container        read that container; insert proposals only
    owner     a container, or '*'  everything in that container

A login also needs membership of the matching group role (sams_agent,
sams_dreamer, sams_owner), which carries the table privileges. Give each agent
process its own login with one agent grant; that is the Postgres equivalent of
mounting only one tenant's directory.

Provenance (author, source_session, tainted, pending_key, created_by) is
supplied by the client and stamped by the harness; the database cannot verify
it. RLS enforces scope and status, not who wrote a row.

Version numbers and history are kept by triggers, so they hold even for a
client that bypasses the harness: a new `active` row continues from the highest
version the path ever had, every update increments it, and every update or
delete copies the old row to memory_history.

The schema-owning role (the one that runs `install_schema`) bypasses RLS and
MUST NOT be used by agents, dreaming jobs or review tools.

Setup, as a database administrator:

    from sams.postgres_store import install_schema, grant
    install_schema(admin_dsn)                                  # schema "sams"
    grant(admin_dsn, "agent_acme", "agent", "tenant:acme")     # role must exist
    grant(admin_dsn, "dreamer_acme", "dreamer", "tenant:acme")
    grant(admin_dsn, "alice", "owner", "*")

Then each process connects as its own login:

    from sams.postgres_store import PostgresBackend
    backend = PostgresBackend("host=db dbname=app user=agent_acme password=...")

Requires psycopg 3: pip install "safer-agent-memory[postgres]".
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Any, Iterable, Iterator

try:
    import psycopg
    from psycopg import errors as pg_errors
    from psycopg import sql
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
except ImportError as e:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        'PostgresBackend needs psycopg 3: pip install "safer-agent-memory[postgres]"'
    ) from e

from .backend import Entry, Proposal
from .errors import NotFound, PermissionDenied, ScopeError, VersionConflict
from .scopes import Scope

KINDS = ("agent", "dreamer", "owner")
_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")

_TS = "to_char({col} AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')"
_ENTRY_COLS = (
    "scope, path, status, pending_key, description, content, version, author, "
    "source_session, tainted, base_version, " + _TS.format(col="updated_at") + " AS updated_at"
)

# {s} is replaced by the quoted schema name. Roles are cluster-wide, so the
# group roles are created only if missing and may be shared by several schemas.
_SCHEMA = """
DO $roles$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sams_agent') THEN CREATE ROLE sams_agent NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sams_dreamer') THEN CREATE ROLE sams_dreamer NOLOGIN; END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sams_owner') THEN CREATE ROLE sams_owner NOLOGIN; END IF;
END $roles$;

CREATE SCHEMA IF NOT EXISTS {s};
REVOKE ALL ON SCHEMA {s} FROM PUBLIC;
GRANT USAGE ON SCHEMA {s} TO sams_agent, sams_dreamer, sams_owner;

CREATE TABLE IF NOT EXISTS {s}.sams_grants (
    role_name  name NOT NULL,
    kind       text NOT NULL CHECK (kind IN ('agent', 'dreamer', 'owner')),
    container  text NOT NULL CHECK (container = 'org' OR container = '*'
                                    OR container ~ '^(project|tenant):[A-Za-z0-9][A-Za-z0-9_.-]{{0,63}}$'),
    PRIMARY KEY (role_name, kind, container),
    CHECK (kind = 'owner' OR container <> '*'),
    CHECK (kind <> 'agent' OR container <> 'org')
);
-- One assigned scope per agent login (4.1: at most one tenant at a time).
CREATE UNIQUE INDEX IF NOT EXISTS sams_grants_one_agent_scope ON {s}.sams_grants (role_name) WHERE kind = 'agent';

CREATE TABLE IF NOT EXISTS {s}.memories (
    container      text    NOT NULL,
    scope          text    NOT NULL,
    path           text    NOT NULL,
    status         text    NOT NULL CHECK (status IN ('active', 'pending', 'retired')),
    pending_key    text    NOT NULL DEFAULT '',
    description    text    NOT NULL,
    content        text    NOT NULL,
    version        integer NOT NULL,
    author         text    NOT NULL,
    source_session text,
    tainted        boolean NOT NULL DEFAULT false,
    base_version   integer NOT NULL DEFAULT 0,
    updated_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    fts            tsvector GENERATED ALWAYS AS (
                       to_tsvector('simple', path || ' ' || description || ' ' || content)) STORED,
    PRIMARY KEY (container, scope, path, status, pending_key),
    -- run scratchpads live inside a project or tenant container, never in org
    CHECK (scope = container OR (scope LIKE 'run:%' AND container <> 'org'))
);
CREATE INDEX IF NOT EXISTS memories_fts_idx ON {s}.memories USING gin (fts);

CREATE TABLE IF NOT EXISTS {s}.memory_history (
    id             bigserial PRIMARY KEY,
    op             text NOT NULL,
    changed_at     timestamptz NOT NULL DEFAULT clock_timestamp(),
    container      text NOT NULL,
    scope text, path text, status text, pending_key text, description text, content text,
    version integer, author text, source_session text, tainted boolean, base_version integer,
    updated_at timestamptz
);
CREATE INDEX IF NOT EXISTS memory_history_path_idx ON {s}.memory_history (container, scope, path);

CREATE TABLE IF NOT EXISTS {s}.proposals (
    id           bigserial PRIMARY KEY,
    container    text NOT NULL,
    scope        text NOT NULL,
    path         text NOT NULL,
    description  text NOT NULL,
    content      text NOT NULL,
    diff         text NOT NULL,
    base_version integer NOT NULL DEFAULT 0,
    evidence     jsonb NOT NULL,
    occurrences  integer NOT NULL DEFAULT 1,
    rationale    text NOT NULL DEFAULT '',
    flags        jsonb NOT NULL,
    created_by   text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
    decision     text CHECK (decision IN ('accepted', 'rejected')),
    decided_by   text,
    decided_at   timestamptz,
    note         text NOT NULL DEFAULT '',
    CHECK (scope = container)
);
CREATE INDEX IF NOT EXISTS proposals_container_idx ON {s}.proposals (container, id);

-- ---- versions and history (run as the schema owner, below RLS) ---------
CREATE OR REPLACE FUNCTION {s}.sams_stamp() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $fn$
BEGIN
    IF TG_OP = 'INSERT' THEN
        -- Version numbers never restart for a path (retire, then write again).
        IF NEW.status = 'active' THEN
            SELECT coalesce(max(v), 0) + 1 INTO NEW.version FROM (
                SELECT version AS v FROM {s}.memories
                 WHERE container = NEW.container AND scope = NEW.scope AND path = NEW.path
                   AND status IN ('active', 'retired')
                UNION ALL
                SELECT version FROM {s}.memory_history
                 WHERE container = NEW.container AND scope = NEW.scope AND path = NEW.path
                   AND status IN ('active', 'retired')) AS known;
        ELSE
            NEW.version := 1;
        END IF;
    ELSE
        IF (NEW.container, NEW.scope, NEW.path, NEW.status, NEW.pending_key)
           IS DISTINCT FROM (OLD.container, OLD.scope, OLD.path, OLD.status, OLD.pending_key) THEN
            RAISE EXCEPTION 'memory entry keys are immutable; write a new entry instead'
                USING ERRCODE = 'insufficient_privilege';
        END IF;
        NEW.version := OLD.version + 1;
    END IF;
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END $fn$;

CREATE OR REPLACE FUNCTION {s}.sams_history() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog AS $fn$
BEGIN
    INSERT INTO {s}.memory_history (op, container, scope, path, status, pending_key, description, content,
                                    version, author, source_session, tainted, base_version, updated_at)
    VALUES (lower(TG_OP), OLD.container, OLD.scope, OLD.path, OLD.status, OLD.pending_key, OLD.description,
            OLD.content, OLD.version, OLD.author, OLD.source_session, OLD.tainted, OLD.base_version,
            OLD.updated_at);
    RETURN NULL;
END $fn$;

DROP TRIGGER IF EXISTS memories_stamp ON {s}.memories;
CREATE TRIGGER memories_stamp BEFORE INSERT OR UPDATE ON {s}.memories
    FOR EACH ROW EXECUTE FUNCTION {s}.sams_stamp();
DROP TRIGGER IF EXISTS memories_history ON {s}.memories;
CREATE TRIGGER memories_history AFTER UPDATE OR DELETE ON {s}.memories
    FOR EACH ROW EXECUTE FUNCTION {s}.sams_history();

-- Proposals are immutable once stored, except for recording the decision once.
CREATE OR REPLACE FUNCTION {s}.sams_proposal_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog AS $fn$
BEGIN
    IF OLD.decision IS NOT NULL
       OR (NEW.id, NEW.container, NEW.scope, NEW.path, NEW.description, NEW.content, NEW.diff, NEW.base_version,
           NEW.evidence, NEW.occurrences, NEW.rationale, NEW.flags, NEW.created_by, NEW.created_at)
          IS DISTINCT FROM
          (OLD.id, OLD.container, OLD.scope, OLD.path, OLD.description, OLD.content, OLD.diff, OLD.base_version,
           OLD.evidence, OLD.occurrences, OLD.rationale, OLD.flags, OLD.created_by, OLD.created_at) THEN
        RAISE EXCEPTION 'proposals are immutable; only an undecided proposal''s decision can be set'
            USING ERRCODE = 'insufficient_privilege';
    END IF;
    NEW.decided_at := clock_timestamp();
    RETURN NEW;
END $fn$;

DROP TRIGGER IF EXISTS proposals_guard ON {s}.proposals;
CREATE TRIGGER proposals_guard BEFORE UPDATE ON {s}.proposals
    FOR EACH ROW EXECUTE FUNCTION {s}.sams_proposal_guard();

-- ---- row-level security --------------------------------------------------
-- Helpers run as the caller (not SECURITY DEFINER), so current_user is the login
-- making the query, and sams_grants' own policy shows each login only its rows.
CREATE OR REPLACE FUNCTION {s}.sams_has(want_kind text, c text) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog AS $fn$
    SELECT EXISTS (SELECT 1 FROM {s}.sams_grants g
                    WHERE g.role_name = current_user AND g.kind = want_kind
                      AND (g.container = c OR (g.kind = 'owner' AND g.container = '*')))
$fn$;

CREATE OR REPLACE FUNCTION {s}.sams_can_read(c text) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog AS $fn$
    SELECT {s}.sams_has('owner', c) OR {s}.sams_has('dreamer', c) OR {s}.sams_has('agent', c)
        OR (c = 'org' AND EXISTS (SELECT 1 FROM {s}.sams_grants g
                                   WHERE g.role_name = current_user AND g.kind = 'agent'))
$fn$;

CREATE OR REPLACE FUNCTION {s}.sams_agent_may_write(c text, sc text, st text) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog AS $fn$
    SELECT c <> 'org' AND {s}.sams_has('agent', c)
       AND ((st = 'pending' AND sc = c) OR (st = 'active' AND sc LIKE 'run:%'))
$fn$;

ALTER TABLE {s}.sams_grants ENABLE ROW LEVEL SECURITY;
ALTER TABLE {s}.memories ENABLE ROW LEVEL SECURITY;
ALTER TABLE {s}.memory_history ENABLE ROW LEVEL SECURITY;
ALTER TABLE {s}.proposals ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS own_grants ON {s}.sams_grants;
CREATE POLICY own_grants ON {s}.sams_grants FOR SELECT USING (role_name = current_user);

DROP POLICY IF EXISTS mem_read ON {s}.memories;
CREATE POLICY mem_read ON {s}.memories FOR SELECT USING ({s}.sams_can_read(container));
DROP POLICY IF EXISTS mem_agent_insert ON {s}.memories;
CREATE POLICY mem_agent_insert ON {s}.memories FOR INSERT TO sams_agent
    WITH CHECK ({s}.sams_agent_may_write(container, scope, status));
DROP POLICY IF EXISTS mem_agent_update ON {s}.memories;
CREATE POLICY mem_agent_update ON {s}.memories FOR UPDATE TO sams_agent
    USING ({s}.sams_agent_may_write(container, scope, status))
    WITH CHECK ({s}.sams_agent_may_write(container, scope, status));
DROP POLICY IF EXISTS mem_owner ON {s}.memories;
CREATE POLICY mem_owner ON {s}.memories FOR ALL TO sams_owner
    USING ({s}.sams_has('owner', container)) WITH CHECK ({s}.sams_has('owner', container));

DROP POLICY IF EXISTS hist_owner_read ON {s}.memory_history;
CREATE POLICY hist_owner_read ON {s}.memory_history FOR SELECT TO sams_owner
    USING ({s}.sams_has('owner', container));
DROP POLICY IF EXISTS hist_owner_clear_runs ON {s}.memory_history;
CREATE POLICY hist_owner_clear_runs ON {s}.memory_history FOR DELETE TO sams_owner
    USING ({s}.sams_has('owner', container) AND scope LIKE 'run:%');

DROP POLICY IF EXISTS prop_dreamer_read ON {s}.proposals;
CREATE POLICY prop_dreamer_read ON {s}.proposals FOR SELECT TO sams_dreamer
    USING ({s}.sams_has('dreamer', container));
DROP POLICY IF EXISTS prop_dreamer_insert ON {s}.proposals;
CREATE POLICY prop_dreamer_insert ON {s}.proposals FOR INSERT TO sams_dreamer
    WITH CHECK ({s}.sams_has('dreamer', container) AND decision IS NULL AND decided_by IS NULL);
DROP POLICY IF EXISTS prop_owner ON {s}.proposals;
CREATE POLICY prop_owner ON {s}.proposals FOR ALL TO sams_owner
    USING ({s}.sams_has('owner', container)) WITH CHECK ({s}.sams_has('owner', container));

-- ---- table privileges (RLS narrows these to rows) -------------------------
REVOKE ALL ON ALL TABLES IN SCHEMA {s} FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA {s} FROM PUBLIC;
GRANT EXECUTE ON FUNCTION {s}.sams_has(text, text), {s}.sams_can_read(text),
    {s}.sams_agent_may_write(text, text, text) TO sams_agent, sams_dreamer, sams_owner;
GRANT SELECT ON {s}.sams_grants TO sams_agent, sams_dreamer, sams_owner;
GRANT SELECT, INSERT, UPDATE ON {s}.memories TO sams_agent;
GRANT SELECT ON {s}.memories TO sams_dreamer;
GRANT SELECT, INSERT, UPDATE, DELETE ON {s}.memories TO sams_owner;
GRANT SELECT, DELETE ON {s}.memory_history TO sams_owner;
GRANT SELECT, INSERT ON {s}.proposals TO sams_dreamer;
GRANT SELECT, INSERT, UPDATE ON {s}.proposals TO sams_owner;
GRANT USAGE ON SEQUENCE {s}.proposals_id_seq TO sams_dreamer, sams_owner;
"""


def _schema_ident(schema: str) -> str:
    if not _IDENT.fullmatch(schema):
        raise ValueError(f"invalid schema name {schema!r}; use lowercase letters, digits and _")
    return schema


def schema_sql(schema: str = "sams") -> str:
    """The DDL `install_schema` runs, for review or for a migration tool."""
    return _SCHEMA.replace("{s}", f'"{_schema_ident(schema)}"').replace("{{", "{").replace("}}", "}")


def install_schema(admin_conninfo: str, schema: str = "sams") -> None:
    """Create the schema, tables, triggers, policies and group roles. Idempotent.

    Re-running it re-applies functions, triggers, policies and privileges; it
    does not migrate columns of existing tables. Run as the role that will own
    the tables (not one your agents use).
    """
    with psycopg.connect(admin_conninfo, autocommit=True) as conn:
        conn.execute(schema_sql(schema))


def grant(admin_conninfo: str, role: str, kind: str, container: str, schema: str = "sams") -> None:
    """Bind an existing login role to a SAMS role and container, and add it to the group role.

    Re-granting the same binding is a no-op. Binding an agent login to a second
    container raises (psycopg.errors.UniqueViolation): an agent holds one scope.
    """
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if container != "*":
        container = str(Scope.parse(container))
        if Scope.parse(container).kind == "run":
            raise ScopeError("grant a container scope (org, project or tenant), not a run")
    s = sql.Identifier(_schema_ident(schema))
    with psycopg.connect(admin_conninfo, autocommit=True) as conn, conn.transaction():
        conn.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(f"sams_{kind}"), sql.Identifier(role)))
        conn.execute(
            sql.SQL("INSERT INTO {}.sams_grants (role_name, kind, container) VALUES (%s, %s, %s) "
                    "ON CONFLICT (role_name, kind, container) DO NOTHING").format(s),
            (role, kind, container),
        )


def revoke(admin_conninfo: str, role: str, kind: str, container: str, schema: str = "sams") -> None:
    """Remove one binding. Group membership is left for the administrator to manage."""
    s = sql.Identifier(_schema_ident(schema))
    with psycopg.connect(admin_conninfo, autocommit=True) as conn:
        conn.execute(sql.SQL("DELETE FROM {}.sams_grants WHERE role_name=%s AND kind=%s AND container=%s").format(s),
                     (role, kind, container))


def _entry(r: dict[str, Any]) -> Entry:
    return dict(r)


class PostgresBackend:
    """Postgres storage with row-level security. One connection per operation.

    `conninfo` is a libpq connection string or URL for the login this process
    uses (never the schema owner). `read_only`
    lists containers this process must refuse to write, as a library-level
    check on top of the database's own policies.
    """

    def __init__(self, conninfo: str, *, schema: str = "sams",
                 read_only: Iterable[str] = ()):
        self._conninfo = conninfo
        self.schema = _schema_ident(schema)
        self.read_only = {str(Scope.parse(c)) for c in read_only}
        q = f'"{self.schema}"'
        self._t_mem, self._t_hist, self._t_prop = f"{q}.memories", f"{q}.memory_history", f"{q}.proposals"

    # ---- connections -------------------------------------------------
    @contextmanager
    def _conn(self) -> Iterator["psycopg.Connection[Any]"]:
        conn = psycopg.connect(self._conninfo, autocommit=True, row_factory=dict_row)
        try:
            yield conn
        except pg_errors.InsufficientPrivilege as e:
            raise PermissionDenied(f"refused by the database: {e.diag.message_primary or e}") from e
        finally:
            conn.close()

    @staticmethod
    def _c(container: str) -> str:
        s = Scope.parse(container)
        if s.kind == "run":
            raise ScopeError("a container is org, project:<id> or tenant:<id>")
        return str(s)

    def _writable(self, container: str) -> str:
        c = self._c(container)
        if c in self.read_only:
            raise PermissionDenied(f"{c} is read-only for this process")
        return c

    # ---- entries -----------------------------------------------------
    def get(self, container, scope, path, status="active", pending_key=""):
        with self._conn() as conn:
            r = conn.execute(
                f"SELECT {_ENTRY_COLS} FROM {self._t_mem} "
                "WHERE container=%s AND scope=%s AND path=%s AND status=%s AND pending_key=%s",
                (self._c(container), scope, path, status, pending_key),
            ).fetchone()
        return _entry(r) if r else None

    def list(self, container, scope=None, statuses=("active",)):
        sql_ = f"SELECT {_ENTRY_COLS} FROM {self._t_mem} WHERE container=%s AND status = ANY(%s)"
        args: list[Any] = [self._c(container), list(statuses)]
        if scope is not None:
            sql_ += " AND scope=%s"
            args.append(scope)
        with self._conn() as conn:
            rows = conn.execute(sql_ + ' ORDER BY scope COLLATE "C", path COLLATE "C"', args).fetchall()
        return [_entry(r) for r in rows]

    def search(self, container, scope, query, statuses=("active",), limit=20):
        # plainto_tsquery treats the input as plain words, so it cannot inject query syntax.
        text = "".join(ch if ch.isprintable() else " " for ch in query).strip()
        if not text:
            return []
        with self._conn() as conn:
            rows = conn.execute(
                f"""SELECT scope, path, description, version, status,
                           ts_headline('simple', content, q,
                                       'StartSel=[, StopSel=], MaxWords=12, MinWords=4, ShortWord=1') AS snippet
                    FROM {self._t_mem}, plainto_tsquery('simple', %s) AS q
                    WHERE container=%s AND scope=%s AND status = ANY(%s) AND fts @@ q
                    ORDER BY ts_rank(fts, q) DESC, path COLLATE "C" LIMIT %s""",
                (text, self._c(container), scope, list(statuses), limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def write(self, container, scope, path, *, status, pending_key, content, description,
              author, source_session, tainted, expected_version, base_version=0):
        c = self._writable(container)
        key = (c, scope, path, status, pending_key)
        where = "container=%s AND scope=%s AND path=%s AND status=%s AND pending_key=%s"
        with self._conn() as conn:
            try:
                with conn.transaction():
                    if expected_version == 0:
                        # Versions are assigned by the database trigger.
                        conn.execute(
                            f"""INSERT INTO {self._t_mem} (container, scope, path, status, pending_key, description,
                                    content, version, author, source_session, tainted, base_version)
                                VALUES (%s,%s,%s,%s,%s,%s,%s,0,%s,%s,%s,%s)""",
                            (*key, description, content, author, source_session, bool(tainted), base_version),
                        )
                    else:
                        n = conn.execute(
                            f"""UPDATE {self._t_mem} SET description=%s, content=%s, author=%s, source_session=%s,
                                    tainted=%s, base_version=%s
                                WHERE {where} AND version=%s""",
                            (description, content, author, source_session, bool(tainted), base_version,
                             *key, expected_version),
                        ).rowcount
                        if n != 1:
                            raise _Retry()
            except pg_errors.UniqueViolation:
                actual = self._version(conn, key)
                raise VersionConflict(scope, path, expected_version, actual) from None
            except _Retry:
                actual = self._version(conn, key)
                if actual != expected_version:
                    raise VersionConflict(scope, path, expected_version, actual) from None
                raise PermissionDenied(f"{c}: {scope}/{path} ({status}) is not writable by this login") from None
            r = conn.execute(f"SELECT {_ENTRY_COLS} FROM {self._t_mem} WHERE {where}", key).fetchone()
        if r is None:  # written, but this login may not read it back
            raise PermissionDenied(f"{c}: wrote {scope}/{path} but cannot read it back")
        return _entry(r)

    def _version(self, conn: "psycopg.Connection[Any]", key: tuple) -> int:
        r = conn.execute(
            f"SELECT version FROM {self._t_mem} "
            "WHERE container=%s AND scope=%s AND path=%s AND status=%s AND pending_key=%s", key,
        ).fetchone()
        return int(r["version"]) if r else 0

    def delete(self, container, scope, path, status, pending_key=""):
        c = self._writable(container)
        with self._conn() as conn:
            n = conn.execute(
                f"DELETE FROM {self._t_mem} "
                "WHERE container=%s AND scope=%s AND path=%s AND status=%s AND pending_key=%s",
                (c, scope, path, status, pending_key),
            ).rowcount
        if not n:
            raise NotFound(f"{scope}/{path} ({status})")

    def history(self, container, scope, path):
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT id, op, {_TS.format(col='changed_at')} AS changed_at, {_ENTRY_COLS} FROM {self._t_hist} "
                "WHERE container=%s AND scope=%s AND path=%s ORDER BY id",
                (self._c(container), scope, path),
            ).fetchall()
        return [_entry(r) for r in rows]

    def run_scopes(self, container):
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT scope COLLATE \"C\" AS scope FROM {self._t_mem} "
                "WHERE container=%s AND scope LIKE 'run:%%' ORDER BY 1",
                (self._c(container),),
            ).fetchall()
        return [r["scope"] for r in rows]

    def clear_runs(self, container):
        c = self._writable(container)
        with self._conn() as conn, conn.transaction():
            n = conn.execute(f"DELETE FROM {self._t_mem} WHERE container=%s AND scope LIKE 'run:%%'", (c,)).rowcount
            conn.execute(f"DELETE FROM {self._t_hist} WHERE container=%s AND scope LIKE 'run:%%'", (c,))
        return n

    # ---- proposals ---------------------------------------------------
    _PROP_COLS = (
        "id, scope, path, description, content, diff, base_version, evidence, occurrences, rationale, flags, "
        "created_by, " + _TS.format(col="created_at") + " AS created_at, decision, decided_by, "
        + _TS.format(col="decided_at") + " AS decided_at, note"
    )

    def add_proposal(self, container, proposal: Proposal) -> int:
        c = self._c(container)  # proposals are writable even where memory is read-only
        with self._conn() as conn:
            r = conn.execute(
                f"""INSERT INTO {self._t_prop} (container, scope, path, description, content, diff, base_version,
                        evidence, occurrences, rationale, flags, created_by)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (c, proposal["scope"], proposal["path"], proposal["description"], proposal["content"],
                 proposal["diff"], proposal.get("base_version", 0), Jsonb(list(proposal["evidence"])),
                 proposal["occurrences"], proposal.get("rationale", ""), Jsonb(list(proposal["flags"])),
                 proposal["created_by"]),
            ).fetchone()
        return int(r["id"])  # type: ignore[index]

    def list_proposals(self, container, undecided_only=True):
        sql_ = f"SELECT {self._PROP_COLS} FROM {self._t_prop} WHERE container=%s"
        if undecided_only:
            sql_ += " AND decision IS NULL"
        with self._conn() as conn:
            return [dict(r) for r in conn.execute(sql_ + " ORDER BY id", (self._c(container),)).fetchall()]

    def get_proposal(self, container, proposal_id):
        with self._conn() as conn:
            r = conn.execute(f"SELECT {self._PROP_COLS} FROM {self._t_prop} WHERE container=%s AND id=%s",
                             (self._c(container), proposal_id)).fetchone()
        return dict(r) if r else None

    def decide_proposal(self, container, proposal_id, decision, decided_by, note=""):
        with self._conn() as conn:
            n = conn.execute(
                f"UPDATE {self._t_prop} SET decision=%s, decided_by=%s, note=%s "
                "WHERE container=%s AND id=%s AND decision IS NULL",
                (decision, decided_by, note, self._c(container), proposal_id),
            ).rowcount
        if not n:
            raise NotFound(f"undecided proposal {proposal_id}")


class _Retry(Exception):
    """Internal: a conditional update matched no row."""
