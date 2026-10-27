"""Derived SQLite search index over file-backed (markdown) memory. STANDARD.md 6.4.

Markdown stays the source of truth. The index is disposable: gitignore it,
rebuild it from a SessionStart or post-merge hook, delete it whenever you like.

Each file's description is its first non-empty line, with a leading '#',
'description:' or HTML comment marker stripped. Files under notes/ are
session-writable scratch and are labelled trust="scratch"; everything else is
"curated". Search results carry the label so callers can tell them apart.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

DEFAULT_GLOBS = ("memory/**/*.md", "**/CLAUDE.md", ".claude/skills/**/SKILL.md", "notes/**/*.md")
_SKIP = {".git", "node_modules", ".venv", "venv", "__pycache__"}


def _description(text: str) -> str:
    for line in text.splitlines():
        line = line.strip()
        if not line or line == "---":
            continue
        for prefix in ("description:", "<!--", "#"):
            if line.lower().startswith(prefix):
                line = line[len(prefix):].lstrip("# ").strip()
        return line.removesuffix("-->").strip()[:200]
    return ""


def build_index(root: str | Path, db: str | Path = ".memory.db", globs: Iterable[str] = DEFAULT_GLOBS) -> int:
    root = Path(root).resolve()
    db = Path(db) if Path(db).is_absolute() else root / db
    tmp = db.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    conn.execute("CREATE VIRTUAL TABLE docs USING fts5(path, description, content, trust UNINDEXED)")
    seen: set[Path] = set()
    for pattern in globs:
        for f in root.glob(pattern):
            if f in seen or not f.is_file() or _SKIP.intersection(f.relative_to(root).parts):
                continue
            seen.add(f)
            text = f.read_text(encoding="utf-8", errors="replace")
            rel = f.relative_to(root)
            trust = "scratch" if rel.parts[0] == "notes" else "curated"
            conn.execute("INSERT INTO docs VALUES (?,?,?,?)", (str(rel), _description(text), text, trust))
    conn.commit()
    conn.close()
    tmp.replace(db)
    return len(seen)


def search_index(root: str | Path, query: str, db: str | Path = ".memory.db", limit: int = 10) -> list[dict]:
    root = Path(root).resolve()
    db = Path(db) if Path(db).is_absolute() else root / db
    if not db.exists():
        build_index(root, db)
    query = "".join(ch if ch.isprintable() else " " for ch in query)
    terms = " ".join(f'"{t}"' for t in query.replace('"', " ").split())
    if not terms:
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT path, description, trust, snippet(docs, 2, '[', ']', '...', 12) AS snippet "
        "FROM docs WHERE docs MATCH ? ORDER BY rank LIMIT ?",
        (terms, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
