# Agent Memory Standard (AMS)

**Security-first memory for AI agents.** Scoped memory tiers, taint tracking, tenant isolation and human-reviewed consolidation, as a short standard, a zero-dependency Python reference implementation, a conformance test suite and a Claude Code plugin that applies it to your project.

> Your agent's memory is a persistence mechanism for prompt injection. One poisoned web page, scan result or email, written to memory, is read by every future session. AMS is a set of rules and tests that stops that from happening.

## Why

Most agent memory projects focus on recall: how to store and find the right memory. AMS focuses on governance:

- **Scopes.** `org`, `project`, `tenant`, `run`. Agents write freely only to their own run scratchpad.
- **Taint.** Sessions that read outsider-controlled content are marked by the harness, not the agent. Their writes never reach curated memory unreviewed.
- **Tenant isolation.** One customer's data never reaches another's memory, or shared memory.
- **Dreaming.** A scheduled job reviews many sessions and proposes changes (redacted, flagged, with evidence). A human accepts or rejects each one.
- **Guardrails.** Versioning with provenance, optimistic locking, permissions enforced below the agent.

It builds on the context-engineering practices Anthropic has described publicly (memory as files, progressive disclosure, out-of-band consolidation) and adds the security controls needed when agents handle untrusted input and multiple tenants. AMS is vendor-neutral and works with any model or agent framework.

## What's in the repo

| Path | What it is |
| --- | --- |
| [`STANDARD.md`](STANDARD.md) | The standard (MUST/SHOULD rules, ~10 minute read) |
| [`ams/`](ams/) | Python reference implementation: harness, SQLite backend, redaction, CLI. Stdlib only |
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
pip install agent-memory-standard        # or: pip install -e . from a clone
```

```python
from ams import SQLiteBackend, AgentSession, DreamerSession, Owner

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
ams review list   --base memory --scope tenant:acme
ams review show   --base memory --scope tenant:acme 3
ams review accept --base memory --scope tenant:acme 3
ams pending list  --base memory --scope tenant:acme
ams pending show  --base memory --scope tenant:acme waf.md s-001
ams history       --base memory --scope tenant:acme methodology/waf.md
```

### Claude Code sessions only

For a repo where the only "agent" is your coding assistant, copy `templates/claude-code/` into the repo: lean `CLAUDE.md`, deny rules on curated files, a `notes/` scratchpad, and `dream.sh`, a weekly job that proposes memory changes on a `dream/<date>` branch for you to review like a PR.

## Conformance

```bash
pip install -e ".[test]"
pytest
```

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
| C12 | The owner promotes exactly what they reviewed; later changes cause a conflict |

To test another backend, implement `ams.backend.Backend` and add a factory to `conformance/conftest.py`.

## Storage forms

| Form | Use when |
| --- | --- |
| File-backed markdown in git | One repo or one agent, including coding-assistant memory |
| File-backed + derived SQLite index (`ams index`) | Large repos where grep over memory gets noisy |
| SQLite, one directory per scope (`SQLiteBackend`) | All agents on one host; isolation via Docker mounts |
| Postgres with row-level security | Agents across hosts or serverless (reference backend planned for v0.2) |

## Roadmap

- v0.2: Postgres/Supabase backend with RLS policies; MCP server exposing the four tools; Postgres in the conformance matrix.
- v0.3: TypeScript port of the harness; adapters for common agent frameworks.

## Status

Draft (v0.1). The rules are stable enough to use; names and APIs may change before 1.0. Redaction and flagging are pattern-based safety nets, not guarantees; the human review gate is the control. Feedback and conformance reports from real deployments are the most useful contributions.

## License

Code: [Apache-2.0](LICENSE). Standard text (`STANDARD.md`, templates and docs): [CC BY 4.0](LICENSE-docs).

Maintained by [Final Frontier Ventures](https://ffvlk.com).
