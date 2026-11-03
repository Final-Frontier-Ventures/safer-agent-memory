"""MCP server: the four tools over MCP, with the harness rules intact (C1, C2, C3, C4, C7, C9).

Runs when the [mcp] extra is installed; skipped otherwise.
"""

from __future__ import annotations

import json
import os
import sys

import pytest

pytest.importorskip("mcp")

import anyio  # noqa: E402
from mcp import Client, StdioServerParameters  # noqa: E402

from sams import AgentSession, Owner, SQLiteBackend  # noqa: E402
from sams.mcp_server import DEFAULT_TAINT_REASON, INSTRUCTIONS, build_server  # noqa: E402


@pytest.fixture
def base(tmp_path):
    owner = Owner(SQLiteBackend(tmp_path))
    owner.write("org", "shared.md", "org lesson", "shared")
    owner.write("tenant:a", "deploy.md", "use compose v2", "deploy steps")
    owner.write("tenant:b", "b.md", "tenant b only", "b")
    return tmp_path


def session(base, *, tainted=True, sid="s1"):
    s = AgentSession(SQLiteBackend(base, read_only=["org"]), session_id=sid, agent="agent-a", assigned="tenant:a")
    if tainted:
        s.mark_tainted(DEFAULT_TAINT_REASON)
    return s


def call(s, *calls):
    """Run tool calls through an in-process MCP client; return (tools, results)."""
    async def go():
        async with Client(build_server(s)) as client:
            tools = (await client.list_tools()).tools
            results = [await client.call_tool(name, args) for name, args in calls]
            return tools, results
    return anyio.run(go)


def payload(result):
    return json.loads(result.content[0].text)


def test_lists_exactly_the_four_tools_with_harness_schemas(base):
    s = session(base)
    tools, _ = call(s)
    assert {t.name: t.input_schema for t in tools} == {x["name"]: x["input_schema"] for x in s.tool_schemas()}
    for t in tools:
        assert t.input_schema["additionalProperties"] is False
        assert {"tainted", "author", "status", "source_session", "pending_key"}.isdisjoint(t.input_schema["properties"])


@pytest.mark.parametrize("extra", [{"status": "active"}, {"tainted": False}, {"author": "owner"}])
def test_provenance_arguments_are_rejected(base, extra):
    s = session(base)
    _, [r] = call(s, ("memory_write", {"scope": "tenant:a", "path": "x.md", "content": "x", "description": "x",
                                       **extra}))
    assert r.is_error and "invalid arguments" in r.content[0].text
    assert Owner(SQLiteBackend(base)).pending("tenant:a") == []


def test_scope_rules_hold_over_mcp(base):
    s = session(base)
    w = {"content": "note", "description": "n"}
    _, (run, assigned, org, other) = call(
        s,
        ("memory_write", {"scope": s.run_scope, "path": "n.md", **w}),
        ("memory_write", {"scope": "tenant:a", "path": "n.md", **w}),
        ("memory_write", {"scope": "org", "path": "n.md", **w}),
        ("memory_write", {"scope": "tenant:b", "path": "n.md", **w}),
    )
    assert payload(run)["status"] == "active"
    assert payload(assigned)["status"] == "pending"
    assert org.is_error and "PermissionDenied" in org.content[0].text
    assert other.is_error and "PermissionDenied" in other.content[0].text
    assert Owner(SQLiteBackend(base)).get("tenant:a", "n.md") is None


def test_reads_and_tenant_isolation(base):
    s = session(base)
    _, (index, read, search, other) = call(
        s,
        ("memory_index", {"scope": "tenant:a"}),
        ("memory_read", {"scope": "org", "path": "shared.md"}),
        ("memory_search", {"scope": "tenant:a", "query": "compose"}),
        ("memory_read", {"scope": "tenant:b", "path": "b.md"}),
    )
    assert payload(index) == [{"path": "deploy.md", "description": "deploy steps", "version": 1}]
    assert payload(read)["content"] == "org lesson"
    assert payload(search)[0]["path"] == "deploy.md"
    assert other.is_error and "tenant b only" not in other.content[0].text


def test_session_is_tainted_by_default(base):
    call(session(base), ("memory_write", {"scope": "tenant:a", "path": "t.md", "content": "x", "description": "x"}))
    call(session(base, tainted=False, sid="s2"),
         ("memory_write", {"scope": "tenant:a", "path": "u.md", "content": "x", "description": "x"}))
    pending = {p["path"]: p for p in Owner(SQLiteBackend(base)).pending("tenant:a")}
    assert pending["t.md"]["tainted"] is True and "tainted-source" in pending["t.md"]["flags"]
    assert pending["u.md"]["tainted"] is False


def test_version_conflict_reaches_the_model(base):
    s = session(base)
    _, [r] = call(s, ("memory_write", {"scope": "tenant:a", "path": "deploy.md", "content": "x", "description": "x",
                                       "expected_version": 0}))
    assert r.is_error and "VersionConflict" in r.content[0].text and "re-read" in r.content[0].text


def test_secrets_are_redacted(base):
    s = session(base)
    call(s, ("memory_write", {"scope": "tenant:a", "path": "k.md", "description": "keys",
                              "content": "DB_PASSWORD=hunter2hunter2"}))
    assert "hunter2" not in Owner(SQLiteBackend(base)).pending("tenant:a")[0]["content"]


def test_instructions_carry_no_memory(base):
    s = session(base)

    async def go():
        async with Client(build_server(s)) as client:
            return client.instructions
    got = anyio.run(go)
    assert got == INSTRUCTIONS
    for text in ("org lesson", "compose", "deploy.md", "tenant:a"):
        assert text not in got


def test_unexpected_errors_do_not_leak(base):
    class Broken(SQLiteBackend):
        def list(self, *a, **k):
            raise RuntimeError("connection to postgresql://agent:s3cret@db/app failed")
    s = AgentSession(Broken(base), session_id="s1", agent="a", assigned="tenant:a")
    _, [r] = call(s, ("memory_index", {"scope": "tenant:a"}))
    assert r.is_error and "s3cret" not in r.content[0].text and "internal error" in r.content[0].text


def test_unknown_tool(base):
    _, [r] = call(session(base), ("memory_delete", {"scope": "tenant:a", "path": "deploy.md"}))
    assert r.is_error


def test_sams_mcp_over_stdio(base):
    """End to end: launch `sams mcp` as a subprocess and talk to it over stdio."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "sams.cli", "mcp", "--base", str(base), "--assigned", "tenant:a", "--agent", "agent-a",
              "--session-id", "stdio1"],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [os.getcwd(), os.environ.get("PYTHONPATH")]))},
    )

    async def go():
        async with Client(params) as client:
            names = sorted(t.name for t in (await client.list_tools()).tools)
            w = await client.call_tool("memory_write", {"scope": "tenant:a", "path": "s.md", "content": "via stdio",
                                                        "description": "s"})
            return names, w
    names, w = anyio.run(go)
    assert names == ["memory_index", "memory_read", "memory_search", "memory_write"]
    assert payload(w)["status"] == "pending"
    p = Owner(SQLiteBackend(base)).pending("tenant:a")[0]
    assert (p["author"], p["pending_key"], p["tainted"]) == ("agent-a", "stdio1", True)


def test_cli_refuses_bad_launch_config(base, capsys):
    from sams.cli import main
    with pytest.raises(SystemExit):
        main(["mcp", "--base", str(base), "--assigned", "org", "--agent", "a"])
    assert "project or tenant" in capsys.readouterr().err
