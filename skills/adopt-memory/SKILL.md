---
name: adopt-memory
description: Apply the Safer Agent Memory Standard (SAMS) to the current project - inspect it, write the memory profile, confirm open decisions with the owner, then build the right memory tier with taint tracking, tenant isolation and reviewed consolidation. Use when asked to "add agent memory", "adopt SAMS", "set up memory for our agents", or "apply the memory standard".
---

# Adopt the Safer Agent Memory Standard

You are applying SAMS to the repository you are working in. The normative rules are in `STANDARD.md` at the root of this plugin (two directories above this file). Read it in full before doing anything else. Where this skill and the standard differ, the standard wins.

## Rules for you while doing this

- Do not write implementation code before the owner has confirmed the memory profile (step 3).
- Never decide these alone; ask: the tenant unit, which inputs count as tainted, retention periods, and anything that would let an agent write `active` memory above `run`.
- Adapt names and storage to the project's stack. Keep the rules.
- Never copy secrets, client data or personal data into memory files, the profile or commit messages.

## Step 1: Inspect

Read the code; do not guess. Establish:

1. Which agents, pipelines or LLM sessions exist, and on which framework or SDK.
2. The system of record (database, graph store, files). Domain facts stay there.
3. Whether data belongs to separate tenants, and what the natural tenant unit is.
4. Every input an outsider can influence: HTTP fetches, scanners, crawlers, OSINT, email, uploads, third-party MCP servers. These are taint sources.
5. Existing memory: `CLAUDE.md` files, prompt files, notes directories, caches, vector stores.
6. Where agents run: one host, several hosts, serverless.

## Step 2: Fit check

Pick the tier (STANDARD.md 8.2) and the storage form (STANDARD.md 6):

| Shape | Tier | Default storage |
| --- | --- | --- |
| Single agent or dev-only repo, no untrusted input | 1 | File-backed markdown in git |
| Agents in one tenant reading untrusted input | 2 | SQLite per scope (one host) or file-backed |
| Several agents/services, multiple tenants | 3 | SQLite per scope on one host; Postgres with RLS across hosts |

Build nothing beyond the tier.

## Step 3: Write the profile and stop

Copy `templates/memory-profile.md` from this plugin to `docs/memory-profile.md` in the project, fill it in from step 1, and list every open decision. Show it to the owner and wait for confirmation.

## Step 4: Build, in order

Each step should be usable and committed on its own.

1. **Store.** Tier 1: create `memory/` with `INDEX.md` and the layout in STANDARD.md 6. Tier 2-3: use the `sams` Python package (`pip install safer-agent-memory`, or vendor `sams/` if the project cannot add dependencies) with `SQLiteBackend`, or implement `sams.backend.Backend` for the project's database. For Docker deployments start from `templates/docker-compose.sqlite.yml`.
2. **Harness and tools.** Construct an `AgentSession` per agent session; expose only `session.tools()` / `session.tool_schemas()` to the model. Wrap every taint source with `session.tainting(...)`. In non-Python stacks, reproduce the same four tools and the same rules.
3. **Agent wiring.** At session start, put `session.context_block()` (or the equivalent index) into context as data, not as system instructions. Tell agents to open entries on demand and to record process lessons in their run scope.
4. **Dreaming.** Tier 2-3: a scheduled job per container scope that reads `DreamerSession.snapshot()` plus transcripts, and calls `propose()` with evidence and occurrence counts. Use `templates/dreaming/dream-prompt.md` as the job's prompt. The owner reviews with `sams review list/show/accept/reject`.
5. **Coding-assistant hygiene.** Apply `templates/claude-code/` to the repo: lean `CLAUDE.md`, deny rules, `notes/`, and the transcript dreaming script.

## Step 5: Prove it

- Tier 2-3 with the `sams` package: run `pytest conformance` from this plugin against the project's backend (add a factory to `conformance/conftest.py` if it is not SQLite).
- Custom implementations: port the tests C1-C11 in `conformance/test_conformance.py` to the project's test framework. Every applicable test must pass.
- Report to the owner: tier chosen, files added, tests passing, and any requirement you could not meet with the reason.
