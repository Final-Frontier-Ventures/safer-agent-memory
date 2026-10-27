"""`sams` command line: owner review, history and the derived file index.

    sams index build [ROOT]              build .memory.db from markdown memory
    sams index search QUERY [--root R]   search it
    sams redact FILE...                  print redacted copies (for transcripts)
    sams review list   --base DIR --scope SCOPE
    sams review show   --base DIR --scope SCOPE ID
    sams review accept --base DIR --scope SCOPE ID --digest SHA256
    sams review reject --base DIR --scope SCOPE ID [--note TEXT]
    sams pending list   --base DIR --scope SCOPE
    sams pending show   --base DIR --scope SCOPE PATH SESSION
    sams pending accept --base DIR --scope SCOPE PATH SESSION --version N
    sams pending reject --base DIR --scope SCOPE PATH SESSION
    sams history --base DIR --scope SCOPE PATH
    sams restore --base DIR --scope SCOPE PATH VERSION
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import Owner, SQLiteBackend
from .file_index import build_index, search_index
from .redact import redact


def _owner(a: argparse.Namespace) -> Owner:
    return Owner(SQLiteBackend(a.base), name=a.owner)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sams", description="Safer Agent Memory Standard tools")
    sub = p.add_subparsers(dest="cmd", required=True)

    ix = sub.add_parser("index", help="derived search index over markdown memory")
    ixs = ix.add_subparsers(dest="action", required=True)
    b = ixs.add_parser("build"); b.add_argument("root", nargs="?", default=".")
    s = ixs.add_parser("search"); s.add_argument("query"); s.add_argument("--root", default=".")

    r = sub.add_parser("redact", help="print redacted copies of files")
    r.add_argument("files", nargs="+")
    r.add_argument("--out", help="write redacted files into this directory instead of stdout")

    def store_args(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--base", required=True, help="directory holding the SQLite scope files")
        sp.add_argument("--scope", required=True)
        sp.add_argument("--owner", default=os.environ.get("SAMS_OWNER", "owner"))

    rv = sub.add_parser("review", help="dreaming proposals")
    rvs = rv.add_subparsers(dest="action", required=True)
    for name in ("list", "show", "accept", "reject"):
        sp = rvs.add_parser(name); store_args(sp)
        if name != "list":
            sp.add_argument("id", type=int)
        if name == "reject":
            sp.add_argument("--note", default="")
        if name == "accept":
            sp.add_argument("--digest", required=True,
                            help="fingerprint of the proposal you reviewed (shown by 'review show')")

    pd = sub.add_parser("pending", help="pending agent writes")
    pds = pd.add_subparsers(dest="action", required=True)
    for name in ("list", "show", "accept", "reject"):
        sp = pds.add_parser(name); store_args(sp)
        if name != "list":
            sp.add_argument("path"); sp.add_argument("session")
        if name == "accept":
            sp.add_argument("--version", type=int, required=True,
                            help="pending version you reviewed (shown by 'pending show')")

    h = sub.add_parser("history"); store_args(h); h.add_argument("path")
    rs = sub.add_parser("restore"); store_args(rs); rs.add_argument("path"); rs.add_argument("version", type=int)

    a = p.parse_args(argv)

    if a.cmd == "index":
        if a.action == "build":
            print(f"indexed {build_index(a.root)} files")
        else:
            for hit in search_index(a.root, a.query):
                print(f"[{hit['trust']}] {hit['path']}: {hit['description']}\n    {hit['snippet']}")
        return 0

    if a.cmd == "redact":
        total = 0
        for f in a.files:
            text, n = redact(Path(f).read_text(encoding="utf-8", errors="replace"))
            total += n
            if a.out:
                out = Path(a.out) / Path(f).name
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(text, encoding="utf-8")
            else:
                sys.stdout.write(text)
        print(f"redactions: {total}", file=sys.stderr)
        return 0

    o = _owner(a)
    if a.cmd == "review":
        if a.action == "list":
            for pr in o.proposals(a.scope):
                flags = ",".join(pr["flags"]) or "-"
                print(f"#{pr['id']}  {pr['path']}  x{pr['occurrences']}  flags={flags}  {pr['description']}")
        elif a.action == "show":
            pr = next((x for x in o.proposals(a.scope, undecided_only=False) if x["id"] == a.id), None)
            if pr is None:
                print("not found", file=sys.stderr); return 1
            print(json.dumps({k: pr[k] for k in ("id", "path", "base_version", "flags", "occurrences",
                                                 "rationale", "evidence")}, indent=2))
            print(pr["diff"])
            if pr["decision"] is None:
                print(f"accept with: sams review accept --base {a.base} --scope {a.scope} {pr['id']} --digest {pr['digest']}")
        elif a.action == "accept":
            e = o.accept_proposal(a.scope, a.id, a.digest); print(f"accepted -> {e['path']} v{e['version']}")
        else:
            o.reject_proposal(a.scope, a.id, a.note); print("rejected")
    elif a.cmd == "pending":
        if a.action == "list":
            for e in o.pending(a.scope):
                flags = ",".join(e["flags"]) or "-"
                print(f"{e['path']}  session={e['pending_key']}  v{e['version']}  base=v{e['base_version']}  "
                      f"flags={flags}  {e['description']}")
        elif a.action == "show":
            e = next((x for x in o.pending(a.scope) if x["path"] == a.path and x["pending_key"] == a.session), None)
            if e is None:
                print("not found", file=sys.stderr); return 1
            print(f"path={e['path']} session={e['pending_key']} pending_version={e['version']} "
                  f"base=v{e['base_version']} author={e['author']} flags={','.join(e['flags']) or '-'}")
            print(f"description: {e['description']}")
            print(e["diff"])
            print(f"accept with: sams pending accept --base {a.base} --scope {a.scope} {a.path} {a.session} --version {e['version']}")
        elif a.action == "accept":
            e = o.accept_pending(a.scope, a.path, a.session, a.version); print(f"accepted -> v{e['version']}")
        else:
            o.reject_pending(a.scope, a.path, a.session); print("rejected")
    elif a.cmd == "history":
        for e in o.history(a.scope, a.path):
            print(f"{e['changed_at']}  {e['op']:6}  {e['status']:8} v{e['version']}  {e['author']}  {e['source_session']}")
    elif a.cmd == "restore":
        e = o.restore(a.scope, a.path, a.version); print(f"restored as v{e['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
