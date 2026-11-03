# Safer Agent Memory Standard (SAMS)

[![CI](https://github.com/Final-Frontier-Ventures/safer-agent-memory/actions/workflows/ci.yml/badge.svg)](https://github.com/Final-Frontier-Ventures/safer-agent-memory/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/safer-agent-memory)](https://pypi.org/project/safer-agent-memory/)
[![License](https://img.shields.io/badge/license-Apache--2.0%20%2F%20CC%20BY%204.0-blue)](#license)

**Security-first memory for AI agents.** Scoped memory tiers, taint tracking, tenant isolation and human-reviewed consolidation, as a short standard, a zero-dependency Python reference implementation, a conformance test suite and a Claude Code plugin that applies it to your project.

> Your agent's memory is a persistence mechanism for prompt injection. One poisoned web page, scan result or email, written to memory, is read by every future session. SAMS is a set of rules and tests that stops that from happening.

## Why

Most agent memory projects focus on recall: how to store and find the right memory. SAMS focuses on governance:

- **Scopes.** `org`, `project`, `tenant`, `run`. Agents write freely only to their own run scratchpad.
- **Taint.** Sessions that read outsider-controlled content are marked by the harness, not the agent. Their writes never reach curated memory unreviewed.
- **Tenant isolation.** One customer's data never reaches another's memory, or shared memory.
- **Dreaming.** A scheduled job reviews many sessions and proposes changes (redacted, flagged, with evidence). A human accepts or rejects each one.
- **Guardrails.** Versioning with provenance, optimistic locking, permissions enforced below the agent.

It builds on the context-engineering practices Anthropic has described publicly (memory as files, progressive disclosure, out-of-band consolidation) and adds the security controls needed when agents handle untrusted input and multiple tenants. SAMS is vendor-neutral and works with any model or agent framework.

## What's in the repo

| Path | What it is |
| --- | --- |
| [`STANDARD.md`](STANDARD.md) | The standard (MUST/SHOULD rules, ~10 minute read) |
| [`sams/`](sams/) | Python reference implementation: harness, SQLite and Postgres backends, redaction, CLI. Core is stdlib only |
| [`conformance/`](conformance/) | Backend-agnostic tests C1-C11. Plug in your backend, run `pytest` |
| [`skills/adopt-memory/`](skills/adopt-memory/SKILL.md) | Claude Code skill: inspects your repo and applies the standard |
| [`templates/`](templates/) | Project profile, Claude Code setup, Docker Compose isolation, dreaming job and prompt |

## Quick start

### Let your coding agent apply it

Install as a Claude Code plugin (or copy `skills/adopt-memory` and `STANDARD.md` into your repo), then in your project:

```
/adopt-memory
```

The skill inspects the project, writes `docs/memory-profile.md`, asks you the decisions it must not make alone (tenant unit, taint sources, retention), then builds the right tier and proves it with the conformance tests.

Not using Claude Code? Give `STANDARD.md` to any coding agent and ask it to follow section 0.

### Use the library

```bash
pip install safer-agent-memory           # import sams; CLI: sams
```

```python
from sams import SQLiteBackend, AgentSession, DreamerSession, Owner

backend = SQLiteBackend("memory", read_only=["org"])
session = AgentSession(backend, session_id="s-001", agent="recon-agent", assigned="tenant:acme")

# Taint sources are wrapped by the harness; calling them marks the session.
fetch = session.tainting(my_http_fetch, reason="web_fetch")

# Give the model only these four tools.
tools, schemas = session.tools(), session.tool_schemas()

# Session start: memory as labelled data, not instructions.
context = session.context_block()
```

Agent writes to `run:s-001` land immediately. Writes to `tenant:acme` are queued as `pending` (redacted, flagged and versioned, so what you accept is exactly what you reviewed). Writes to `org` or another tenant are refused.

```bash
python templates/dreaming/dream.py --base memory --scope tenant:acme --transcripts logs/acme
sams review list   --base memory --scope tenant:acme
sams review show   --base memory --scope tenant:acme 3      # prints the diff and its fingerprint
sams review accept --base memory --scope tenant:acme 3 --digest <fingerprint>
sams pending list  --base memory --scope tenant:acme
sams pending show  --base memory --scope tenant:acme waf.md s-001
sams history       --base memory --scope tenant:acme methodology/waf.md
```

### Postgres, with row-level security

For agents on several hosts or serverless, use the Postgres backend. Row-level security binds each database login to one role and container, so the database itself enforces the scope and status rules: an agent login cannot read another tenant, write `org`, write `active` curated memory or touch proposals, and a dreaming login can only insert proposals, which no SAMS login can change afterwards (acceptance still re-checks the review fingerprint, so even a rewrite by the schema owner is refused). Provenance and taint (`author`, `source_session`, `tainted`) are still stamped by the harness; the database cannot verify them, so give agents only the harness tools, not raw SQL.

```bash
pip install "safer-agent-memory[postgres]"   # adds psycopg 3
```

```python
from sams.postgres_store import PostgresBackend, install_schema, grant

# Once, as the schema owner (an admin role your agents never use):
install_schema(ADMIN_DSN)
grant(ADMIN_DSN, "agent_acme", "agent", "tenant:acme")      # existing login roles
grant(ADMIN_DSN, "dreamer_acme", "dreamer", "tenant:acme")
grant(ADMIN_DSN, "alice", "owner", "*")

# Each process connects as its own login:
backend = PostgresBackend("postgresql://agent_acme:...@db/app")
session = AgentSession(backend, session_id="s-001", agent="recon-agent", assigned="tenant:acme")
```

Review works the same with `--dsn` (or `SAMS_DSN`) in place of `--base`: `sams review list --dsn "$SAMS_DSN" --scope tenant:acme`. `schema_sql()` returns the DDL if you apply migrations with your own tool. On Supabase, create dedicated login roles for agents rather than using the `service_role` key, which bypasses row-level security.

### Claude Code sessions only

For a repo where the only "agent" is your coding assistant, copy `templates/claude-code/` into the repo: lean `CLAUDE.md`, deny rules on curated files, a `notes/` scratchpad, and `dream.sh`, a weekly job that proposes memory changes on a `dream/<date>` branch for you to review like a PR.

## Conformance

```bash
pip install -e ".[test]"
pytest
```

To include the Postgres backend, install `.[test,postgres]` and point `SAMS_TEST_POSTGRES_DSN` at a database where that login may create schemas and roles, for example `postgresql://postgres:postgres@localhost:5432/postgres`. Each test runs in its own schema as a non-superuser login, so row-level security is in force.

| ID | Requirement |
| --- | --- |
| C1 | Agent writes to `run` succeed; writes to `org` are refused |
| C2 | Agent writes above `run` land as `pending` |
| C3 | Taint is recorded and cannot be cleared by the agent |
| C4 | Agents cannot set author, status or taint |
| C5 | Concurrent writes: one wins, one conflicts, nothing is lost |
| C6 | Full history with provenance; one-step restore |
| C7 | Tenant A cannot read or write tenant B |
| C8 | The dreaming role can propose but not write |
| C9 | Planted secrets never reach a stored proposal, diff or pending write |
| C10 | Instruction-like and sensitive proposals are flagged |
| C11 | Pending entries are never served as curated memory; memory text cannot escape its context wrapper |
| C12 | The owner promotes exactly what they reviewed; a later change to a pending write, a proposal or its base is refused |

To test another backend, implement `sams.backend.Backend` and add a factory to `conformance/conftest.py`.

## Storage forms

| Form | Use when |
| --- | --- |
| File-backed markdown in git | One repo or one agent, including coding-assistant memory |
| File-backed + derived SQLite index (`sams index`) | Large repos where grep over memory gets noisy |
| SQLite, one directory per scope (`SQLiteBackend`) | All agents on one host; isolation via Docker mounts |
| Postgres with row-level security (`PostgresBackend`) | Agents across hosts or serverless; isolation via database logins and RLS |

## Roadmap

- v0.2: Postgres backend with RLS policies and Postgres in the conformance matrix (done, unreleased; Supabase notes above, not yet tested there); MCP server exposing the four tools.
- v0.3: TypeScript port of the harness; adapters for common agent frameworks.

## Status

Draft (v0.1). The rules are stable enough to use; names and APIs may change before 1.0. Redaction and flagging are pattern-based safety nets, not guarantees; the human review gate is the control. Feedback and conformance reports from real deployments are the most useful contributions.

## License

Code: [Apache-2.0](LICENSE). Standard text (`STANDARD.md`, templates and docs): [CC BY 4.0](LICENSE-docs).

Maintained by [Final Frontier Ventures](https://ffvlk.com).
