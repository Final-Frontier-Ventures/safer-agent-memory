"""SQLite backend: one database per container scope, each in its own directory
(STANDARD.md section 6).

    <base_dir>/
      org/memory.db                       shared memory; read-only for agents
      project-<id>/memory.db              one project, incl. its sessions' run notes
      tenant-<id>/memory.db               one tenant, incl. its sessions' run notes
      proposals-<stem>/proposals.db       dreaming proposals for that container

Isolation comes from which directories a process can see and write. In Docker,
mount org/ read-only into agent containers and mount only the assigned
tenant's directory (see templates/docker-compose.sqlite.yml). Directories, not
single files, are mounted so SQLite's journal files stay with the database.
Pass the same containers in `read_only` so the library refuses writes even
when the filesystem would allow them.

org.db uses a rollback journal so it can be read from a read-only mount;
other files use WAL for concurrent readers.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator

from .backend import Entry, Proposal
from .errors import NotFound, PermissionDenied, VersionConflict
from .scopes import Scope

_COLUMNS = (
    "scope, path, status, pending_key, description, content, version, author, "
    "source_session, tainted, base_version, updated_at"
)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS memories (
    scope          TEXT    NOT NULL,
    path           TEXT    NOT NULL,
    status         TEXT    NOT NULL CHECK (status IN ('active', 'pending', 'retired')),
    pending_key    TEXT    NOT NULL DEFAULT '',
    description    TEXT    NOT NULL,
    content        TEXT    NOT NULL,
    version        INTEGER NOT NULL,
    author         TEXT    NOT NULL,
    source_session TEXT,
    tainted        INTEGER NOT NULL DEFAULT 0,
    base_version   INTEGER NOT NULL DEFAULT 0,
    updated_at     TEXT    NOT NULL,
    PRIMARY KEY (scope, path, status, pending_key)
);

CREATE TABLE IF NOT EXISTS memory_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    op         TEXT NOT NULL,
    changed_at TEXT NOT NULL,
    scope TEXT, path TEXT, status TEXT, pending_key TEXT, description TEXT, content TEXT,
    version INTEGER, author TEXT, source_session TEXT, tainted INTEGER, base_version INTEGER,
    updated_at TEXT
);

CREATE TRIGGER IF NOT EXISTS memories_history_update AFTER UPDATE ON memories BEGIN
    INSERT INTO memory_history (op, changed_at, {_COLUMNS})
    VALUES ('update', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
            OLD.scope, OLD.path, OLD.status, OLD.pending_key, OLD.description, OLD.content,
            OLD.version, OLD.author, OLD.source_session, OLD.tainted, OLD.base_version, OLD.updated_at);
END;

CREATE TRIGGER IF NOT EXISTS memories_history_delete AFTER DELETE ON memories BEGIN
    INSERT INTO memory_history (op, changed_at, {_COLUMNS})
    VALUES ('delete', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
            OLD.scope, OLD.path, OLD.status, OLD.pending_key, OLD.description, OLD.content,
            OLD.version, OLD.author, OLD.source_session, OLD.tainted, OLD.base_version, OLD.updated_at);
END;

CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    path, description, content, content='memories', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS memories_fts_insert AFTER INSERT ON memories BEGIN
    INSERT INTO memories_fts (rowid, path, description, content)
    VALUES (NEW.rowid, NEW.path, NEW.description, NEW.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_fts_delete AFTER DELETE ON memories BEGIN
    INSERT INTO memories_fts (memories_fts, rowid, path, description, content)
    VALUES ('delete', OLD.rowid, OLD.path, OLD.description, OLD.content);
END;

CREATE TRIGGER IF NOT EXISTS memories_fts_update AFTER UPDATE ON memories BEGIN
    INSERT INTO memories_fts (memories_fts, rowid, path, description, content)
    VALUES ('delete', OLD.rowid, OLD.path, OLD.description, OLD.content);
    INSERT INTO memories_fts (rowid, path, description, content)
    VALUES (NEW.rowid, NEW.path, NEW.description, NEW.content);
END;
"""

_PROPOSALS_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scope       TEXT NOT NULL,
    path        TEXT NOT NULL,
    description TEXT NOT NULL,
    content     TEXT NOT NULL,
    diff        TEXT NOT NULL,
    base_version INTEGER NOT NULL DEFAULT 0,  -- curated version the diff was made against
    evidence    TEXT NOT NULL,          -- JSON list of transcript/session references
    occurrences INTEGER NOT NULL DEFAULT 1,
    rationale   TEXT NOT NULL DEFAULT '',
    flags       TEXT NOT NULL,          -- JSON list
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    decision    TEXT CHECK (decision IN ('accepted', 'rejected')),
    decided_by  TEXT,
    decided_at  TEXT,
    note        TEXT NOT NULL DEFAULT ''
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _row(r: sqlite3.Row) -> Entry:
    d = dict(r)
    if "tainted" in d:
        d["tainted"] = bool(d["tainted"])
    return d


def _fts_query(text: str) -> str:
    """Quote every term so user input cannot inject FTS5 syntax."""
    text = "".join(ch if ch.isprintable() else " " for ch in text)
    terms = [t for t in text.replace('"', " ").split() if t]
    return " ".join(f'"{t}"' for t in terms)


class SQLiteBackend:
    """File-per-scope SQLite storage. Stdlib only."""

    def __init__(self, base_dir: str | Path, read_only: Iterable[str] = ()):
        self.base = Path(base_dir)
        self.read_only = {str(Scope.parse(c)) for c in read_only}

    # ---- connections -------------------------------------------------
    def _file(self, container: str) -> Path:
        return self.base / Scope.parse(container).file_stem / "memory.db"

    def _proposals_file(self, container: str) -> Path:
        return self.base / f"proposals-{Scope.parse(container).file_stem}" / "proposals.db"

    @contextmanager
    def _conn(self, path: Path, *, write: bool, schema: str, journal: str = "WAL") -> Iterator[sqlite3.Connection | None]:
        if not write:
            if not path.exists():
                yield None
                return
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10, isolation_level=None)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, timeout=10, isolation_level=None)
            conn.execute(f"PRAGMA journal_mode={journal}")
            conn.executescript(schema)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def _mem(self, container: str, *, write: bool):
        container = str(Scope.parse(container))
        if write and container in self.read_only:
            raise PermissionDenied(f"{container} is read-only for this process")
        journal = "DELETE" if container == "org" else "WAL"
        return self._conn(self._file(container), write=write, schema=_SCHEMA, journal=journal)

    def _props(self, container: str, *, write: bool):
        return self._conn(self._proposals_file(container), write=write, schema=_PROPOSALS_SCHEMA)

    # ---- entries -----------------------------------------------------
    def get(self, container, scope, path, status="active", pending_key=""):
        with self._mem(container, write=False) as c:
            if c is None:
                return None
            r = c.execute(
                f"SELECT {_COLUMNS} FROM memories WHERE scope=? AND path=? AND status=? AND pending_key=?",
                (scope, path, status, pending_key),
            ).fetchone()
            return _row(r) if r else None

    def list(self, container, scope=None, statuses=("active",)):
        statuses = tuple(statuses)
        marks = ",".join("?" * len(statuses))
        sql = f"SELECT {_COLUMNS} FROM memories WHERE status IN ({marks})"
        args: list = list(statuses)
        if scope is not None:
            sql += " AND scope=?"
            args.append(scope)
        with self._mem(container, write=False) as c:
            if c is None:
                return []
            return [_row(r) for r in c.execute(sql + " ORDER BY scope, path", args)]

    def search(self, container, scope, query, statuses=("active",), limit=20):
        q = _fts_query(query)
        if not q:
            return []
        statuses = tuple(statuses)
        marks = ",".join("?" * len(statuses))
        with self._mem(container, write=False) as c:
            if c is None:
                return []
            rows = c.execute(
                f"""SELECT m.scope, m.path, m.description, m.version, m.status,
                           snippet(memories_fts, 2, '[', ']', '...', 12) AS snippet
                    FROM memories_fts JOIN memories m ON m.rowid = memories_fts.rowid
                    WHERE memories_fts MATCH ? AND m.scope = ? AND m.status IN ({marks})
                    ORDER BY rank LIMIT ?""",
                (q, scope, *statuses, limit),
            )
            return [dict(r) for r in rows]

    def write(self, container, scope, path, *, status, pending_key, content, description,
              author, source_session, tainted, expected_version, base_version=0):
        try:
            with self._mem(container, write=True) as c:
                c.execute("BEGIN IMMEDIATE")
                try:
                    cur = c.execute(
                        "SELECT version FROM memories WHERE scope=? AND path=? AND status=? AND pending_key=?",
                        (scope, path, status, pending_key),
                    ).fetchone()
                    actual = cur["version"] if cur else 0
                    if actual != expected_version:
                        raise VersionConflict(scope, path, expected_version, actual)
                    now = _now()
                    if cur is None:
                        # Version numbers never restart for a path (retire, then write again),
                        # so a version number always names exactly one historical content.
                        floor = c.execute(
                            """SELECT MAX(v) FROM (
                                   SELECT version AS v FROM memories
                                   WHERE scope=? AND path=? AND status IN ('active','retired')
                                   UNION ALL
                                   SELECT version FROM memory_history
                                   WHERE scope=? AND path=? AND status IN ('active','retired'))""",
                            (scope, path, scope, path),
                        ).fetchone()[0] if status == "active" else None
                        c.execute(
                            f"INSERT INTO memories ({_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (scope, path, status, pending_key, description, content, (floor or 0) + 1, author,
                             source_session, int(tainted), base_version, now),
                        )
                    else:
                        c.execute(
                            """UPDATE memories SET description=?, content=?, version=version+1, author=?,
                                   source_session=?, tainted=?, base_version=?, updated_at=?
                               WHERE scope=? AND path=? AND status=? AND pending_key=? AND version=?""",
                            (description, content, author, source_session, int(tainted), base_version,
                             now, scope, path, status, pending_key, expected_version),
                        )
                    c.execute("COMMIT")
                except BaseException:
                    c.execute("ROLLBACK")
                    raise
        except sqlite3.OperationalError as e:
            if "readonly" in str(e).lower() or "unable to open" in str(e).lower():
                raise PermissionDenied(f"{container}: storage is read-only ({e})") from e
            raise
        return self.get(container, scope, path, status, pending_key)  # type: ignore[return-value]

    def delete(self, container, scope, path, status, pending_key=""):
        with self._mem(container, write=True) as c:
            n = c.execute(
                "DELETE FROM memories WHERE scope=? AND path=? AND status=? AND pending_key=?",
                (scope, path, status, pending_key),
            ).rowcount
        if not n:
            raise NotFound(f"{scope}/{path} ({status})")

    def history(self, container, scope, path):
        with self._mem(container, write=False) as c:
            if c is None:
                return []
            return [
                _row(r)
                for r in c.execute(
                    f"SELECT id, op, changed_at, {_COLUMNS} FROM memory_history "
                    "WHERE scope=? AND path=? ORDER BY id",
                    (scope, path),
                )
            ]

    def run_scopes(self, container):
        with self._mem(container, write=False) as c:
            if c is None:
                return []
            return [r[0] for r in c.execute("SELECT DISTINCT scope FROM memories WHERE scope LIKE 'run:%'")]

    def clear_runs(self, container):
        with self._mem(container, write=True) as c:
            n = c.execute("DELETE FROM memories WHERE scope LIKE 'run:%'").rowcount
            c.execute("DELETE FROM memory_history WHERE scope LIKE 'run:%'")
        return n

    # ---- proposals ---------------------------------------------------
    def add_proposal(self, container, proposal: Proposal) -> int:
        with self._props(container, write=True) as c:
            cur = c.execute(
                """INSERT INTO proposals (scope, path, description, content, diff, base_version, evidence,
                                          occurrences, rationale, flags, created_by, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (proposal["scope"], proposal["path"], proposal["description"], proposal["content"],
                 proposal["diff"], proposal.get("base_version", 0), json.dumps(proposal["evidence"]),
                 proposal["occurrences"],
                 proposal.get("rationale", ""), json.dumps(proposal["flags"]), proposal["created_by"], _now()),
            )
            return int(cur.lastrowid)

    def _proposal(self, r: sqlite3.Row) -> Proposal:
        d = dict(r)
        d["evidence"] = json.loads(d["evidence"])
        d["flags"] = json.loads(d["flags"])
        return d

    def list_proposals(self, container, undecided_only=True):
        sql = "SELECT * FROM proposals" + (" WHERE decision IS NULL" if undecided_only else "") + " ORDER BY id"
        with self._props(container, write=False) as c:
            if c is None:
                return []
            return [self._proposal(r) for r in c.execute(sql)]

    def get_proposal(self, container, proposal_id):
        with self._props(container, write=False) as c:
            if c is None:
                return None
            r = c.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
            return self._proposal(r) if r else None

    def decide_proposal(self, container, proposal_id, decision, decided_by, note=""):
        with self._props(container, write=True) as c:
            n = c.execute(
                "UPDATE proposals SET decision=?, decided_by=?, decided_at=?, note=? WHERE id=? AND decision IS NULL",
                (decision, decided_by, _now(), note, proposal_id),
            ).rowcount
        if not n:
            raise NotFound(f"undecided proposal {proposal_id}")
