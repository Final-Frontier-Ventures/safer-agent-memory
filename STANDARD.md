# Agent Memory Standard (AMS)

**Version:** 0.1.0 (draft) · **Status:** open for comment · **License:** CC BY 4.0

AMS defines how AI agents keep durable memory safely: markdown memory in scoped tiers, written freely to a scratchpad, and promoted to curated memory only through a reviewed consolidation step ("dreaming"). It treats memory as an attack surface. A prompt injection that reaches memory stops being a one-off and becomes persistent, so AMS tracks taint, isolates tenants and puts a human review gate in front of every curated change.

AMS is framework- and vendor-neutral. It applies to agents built on any SDK, to MCP servers, and to coding assistants such as Claude Code.

The key words MUST, MUST NOT, SHOULD, SHOULD NOT and MAY are to be read as described in RFC 2119.

---

## 0. If you are an agent implementing this standard

1. Read the whole standard once. Sections 1 to 7 define the model; section 8 is the procedure you follow.
2. Inspect the project before designing anything: its agents, data stores, inputs and stack.
3. Write the project profile (section 8.3) and confirm the open decisions with the owner before writing code.
4. Build only the tier the project needs (section 8.2). Adapt names and storage to the project's stack; keep the rules.
5. Prove it with the conformance tests (section 9).

Decisions you MUST NOT make alone: the tenant unit, what counts as tainted, retention periods, and anything that lets an agent write `active` memory above the `run` scope.

---

## 1. Principles

1. **Memory is files, not magic.** Markdown in scoped paths, searchable with ordinary tools. No vector store until plain search demonstrably fails.
2. **Agents write freely to scratch, never to curated memory.** Promotion is a separate, reviewed step.
3. **Every write has provenance.** Who wrote it, from which session, and whether that session touched untrusted input.
4. **Scope is a security boundary.** One tenant's data never reaches another tenant's memory or shared memory.
5. **Facts live in the system of record; lessons live in memory.** Memory holds how to work, not what the data is.
6. **Promote proven primitives to code.** Once a memory pattern is stable, enforce it in deterministic harness code instead of asking agents to follow it.

---

## 2. Memory model

### 2.1 Scopes

| Scope | Holds | Agents may | Promoted by | Lifetime |
| --- | --- | --- | --- | --- |
| `org` | Methodology, tool quirks, conventions and standards shared across all projects | Read only | Owner, via review | Permanent, versioned |
| `tenant:<id>` | Knowledge specific to one customer, client, account or engagement | Read; write as `pending` | Owner, via review | Life of the tenant relationship |
| `project:<id>` | Architecture, commands, gotchas for one codebase or product | Read; write as `pending` | Owner, via review | Life of the project |
| `run:<id>` | Working notes for one session, job or pipeline run | Read and write freely | Not promoted directly; dreaming mines it | Until the next dreaming pass |

- An implementation MUST support `org`, `project` and `run`. It MUST support `tenant` if it processes data belonging to more than one tenant.
- The **tenant** is whatever must never leak into another: a customer, a client engagement, a user account, a workspace.

### 2.2 Memory status

Every memory entry above `run` has a status:

- `active`: curated, loaded into agent context.
- `pending`: proposed, awaiting review, never loaded as curated memory.
- `retired`: kept for history, not loaded.

Agents MUST NOT be able to create or change `active` entries outside `run`.

**Review integrity.** The owner MUST promote exactly the content they reviewed. Pending entries and proposals carry a version; if the entry, or the curated entry it was based on, changes after review, promotion MUST fail with a conflict rather than apply the newer content. Version numbers for a path MUST NOT restart (for example after an entry is retired and written again), so a version number always identifies one historical content.

### 2.3 Facts versus lessons

Domain facts (records, entities, assets, findings, users) MUST stay in the project's system of record. Memory SHOULD NOT duplicate them. Memory holds lessons, for example "tool X returns noisy results for input type Y" or "run migrations before the seed script".

### 2.4 Files and progressive disclosure

- One topic per entry; markdown content; short paths such as `methodology/<tool>.md`.
- Every entry MUST have a one-line description.
- Agents SHOULD read the scope index (paths and descriptions) first and open entries only when needed.

---

## 3. Guardrails

Once more than one agent or session shares memory, all four guardrails are REQUIRED. A single-agent prototype MAY start with versioning alone.

| Guardrail | Requirement |
| --- | --- |
| Versioning and provenance | Every change MUST be reversible in one step and attributable to a session and an author (agent or human). |
| Concurrency | Concurrent writers MUST NOT silently overwrite each other. Writes MUST be conditional on the version the writer read (optimistic locking); a stale write MUST fail, and the writer re-reads and redrafts. |
| Permissioning | Write access MUST be enforced outside the agent: database roles, file permissions, read-only mounts or tool deny rules. An instruction to the agent is not enforcement. |
| Portability | Memory SHOULD be reachable from every surface the project uses through one small interface (section 6.3). |

If a memory change cannot be traced to a session and rolled back in one step, it does not ship.

---

## 4. Taint and memory poisoning

A session is **tainted** once it has read content an outsider can control.

Typical tainted sources: fetched web pages, third-party API responses, scan and crawl output, scraped or OSINT data, emails and chat messages, uploaded files, third-party MCP tool output, and coding sessions that fetched URLs or ran network tools. When in doubt, a source is tainted.

1. The harness, not the agent, MUST set the taint flag the moment a tainted source or tool is used. Agents MUST NOT be able to clear it.
2. Writes from tainted sessions MUST land as `pending` in every scope above `run`.
3. Pending writes MUST be promoted only through review (section 5). Proposals that change commands, URLs, credential handling, scope boundaries or permissions MUST be flagged for extra scrutiny.
4. Memory MUST be loaded into agent context as labelled data, never as system-level instructions. Text taken from memory MUST be escaped so it cannot close or forge the wrapper that labels it.

### 4.1 Tenant isolation

- A session or job MUST hold at most one tenant scope at a time.
- Dreaming MUST run per scope. Tenant material MAY feed `org` memory only as a reviewed, de-identified lesson.
- Secrets, credentials and personal data MUST NOT be written to memory at any scope. A redaction pass MUST run on agent writes and before any proposal or review diff is stored or shown, covering both the new and the existing content.
- Review flags MUST consider added and removed lines, the description and the rationale, and MUST mark proposals whose evidence comes from tainted sessions.

---

## 5. Dreaming (out-of-band consolidation)

In-band memory has three limits: the agent splits focus between the task and curation, it sees only its own session, and memories go stale. Dreaming is a scheduled job, separate from any agent session, that reviews many sessions and proposes memory changes for human review.

```
Agent session ──► run scratchpad ─┐
      │        ──► pending writes ─┼──► Dreaming job ──► Proposals ──► Owner review ──► active memory
      └──────────► transcripts ────┘                                        │
                                                                            └──► rejected (logged)
```

- **Inputs:** one scope's run notes, pending writes and session transcripts (including tool calls) for the period, plus that scope's current curated memory.
- **Method:** an orchestrator MAY fan out sub-agents over transcript batches; each reports patterns with evidence; the orchestrator merges and de-duplicates.
- **Looks for:** repeated corrections, failed or misconfigured tool calls, facts rediscovered across sessions, missing topics, stale or contradicted entries.
- **Output:** proposals, each a diff with transcript references and an occurrence count. The dreaming job MUST NOT write memory directly.
- **Least privilege:** the job MUST be able to read only the scope it is consolidating and write only proposals.
- **Budget:** the job SHOULD have its own token budget, separate from production runs.
- **Review:** the owner accepts, rejects or edits each proposal. Accepted changes are written by the owner role with the proposal id as provenance. Rejections are kept.
- **Cadence:** weekly per active scope by default; at close-out for time-boxed tenants; skip scopes with no new runs.
- `run` memory consumed by a dreaming pass SHOULD then be deleted.

A proposal rejected repeatedly for the same reason means the prompt or harness needs fixing, not the memory.

---

## 6. Storage forms

Pick by where the agents run. All forms follow the same scopes and rules; only the enforcement point changes.

| Form | Use when | Permissions enforced by |
| --- | --- | --- |
| Postgres (with row-level security) | Agents run on several hosts or serverless; many concurrent writers | Row-level security |
| SQLite, one file per scope | All agents on one host, for example one Docker Compose deployment | Read-only mounts and file permissions; harness for status and taint |
| File-backed (markdown in git) | One repo or one agent, including coding-assistant memory | Tool deny rules and reviewed commits |
| File-backed plus derived SQLite index | Large repos where grep over memory is slow or noisy | As file-backed; the index is derived, never edited |

### 6.1 Record shape (database forms)

| Field | Notes |
| --- | --- |
| `scope`, `path` | Identify the entry |
| `description` | One line, shown in the index |
| `content` | Markdown |
| `status` | `active` · `pending` · `retired` |
| `version` | Integer, incremented on every write |
| `author`, `source_session`, `tainted` | Stamped by the harness, never by the agent |
| `updated_at` | Timestamp |

A history table MUST receive the previous row on every update or delete. A proposals table holds `scope`, `path`, `diff`, `evidence`, `flags` and the review `decision`.

Reference implementation: `ams/sqlite_store.py` (SQLite). A Postgres reference backend is planned for v0.2.

### 6.2 Roles

- `agent`: read `org` and its assigned scope; write `run:*` freely, and its assigned scope only as `pending`.
- `dreamer`: read one scope per job; write proposals only.
- `owner`: full access; the only role that sets `active` outside `run`.

### 6.3 Agent tool interface

The same four tools, whatever the framework or transport:

| Tool | Purpose |
| --- | --- |
| `memory_index(scope)` | Paths and one-line descriptions only |
| `memory_read(scope, path)` | Content and version |
| `memory_search(scope, query)` | Full-text search; returns paths and snippets |
| `memory_write(scope, path, content, description, expected_version)` | Conditional write; the harness stamps author, session, taint and status |

Agents MUST NOT be able to pass `tainted`, `author` or `status`.

### 6.4 Derived index for large repos

A database file MUST NOT be committed as memory: binary changes cannot be reviewed, merged or diffed. Markdown remains the source of truth; a SQLite FTS index MAY be built from it, kept out of version control and rebuilt on demand (`ams index`).

---

## 7. Coding-assistant sessions

Repos developed with a coding assistant apply the file-backed form to the assistant's own sessions, separate from any memory the product uses. For Claude Code:

| Scope | Location | Writable by sessions |
| --- | --- | --- |
| `org` | `~/.claude/CLAUDE.md` | No |
| `project` | `<repo>/CLAUDE.md`, `.claude/skills/`, `memory/` | No; changed by reviewed commit |
| `run` | `<repo>/notes/` | Yes |

- Curated files MUST be protected by deny rules (`templates/claude-code/settings.json`). Tool deny rules cover the assistant's edit tools and a few shell commands, not every possible shell write; for stronger guarantees, make curated files read-only at the filesystem level or require review of every commit that touches them (for example with CODEOWNERS).
- Agent configuration (settings, hooks, commands, sub-agents, MCP server lists) MUST be treated as curated memory.
- `CLAUDE.md` SHOULD hold only what is true across the whole repo; repeatable procedures SHOULD be skills.
- Parallel sessions SHOULD each use their own git worktree.
- Repos holding sensitive or tenant data SHOULD gitignore `notes/`.
- Transcript dreaming (`templates/dreaming/`) proposes changes on a `dream/<date>` branch and MUST NOT commit to the main branch.

---

## 8. Adopting the standard

### 8.1 Inspect

Establish from the code and the owner: which agents and sessions exist and on which framework; the system of record; whether data belongs to separate tenants and what the tenant unit is; which inputs are tainted; and what memory already exists.

### 8.2 Fit check

| Project shape | Build |
| --- | --- |
| Single agent or dev-only repo, no untrusted input | File-backed memory and section 7; versioning only |
| Agents in one tenant that read untrusted input | File- or database-backed; taint flag and pending queue; dreaming per project |
| Several agents or services across multiple tenants | Database-backed; all guardrails; tenant scopes; dreaming per tenant |

### 8.3 Project profile

Write `docs/memory-profile.md` from `templates/memory-profile.md` and confirm it with the owner.

### 8.4 Build order

Each step is usable on its own:

1. Store: layout or schema, history, roles or deny rules.
2. Tools and harness: the four tools; harness stamps provenance, taint and status.
3. Agent wiring: read the scope index at session start; open entries on demand; write process lessons to `run`.
4. Dreaming job and review surface.
5. Coding-assistant hygiene for the repo itself.

---

## 9. Conformance

An implementation conforms at its fit-check tier when the applicable tests pass. The reference suite (`conformance/`) is backend-agnostic: implement the adapter in `conformance/conftest.py` for your backend and run `pytest conformance`.

| ID | Requirement | Tier |
| --- | --- | --- |
| C1 | An agent write to `run` succeeds; the same write to `org` is refused | all |
| C2 | Agent writes above `run` land as `pending`, never `active` | all |
| C3 | Writes from a tainted session carry the taint flag; an agent cannot clear it | 2, 3 |
| C4 | Agents cannot set `tainted`, `author` or `status` through any tool | all |
| C5 | Of two concurrent writes from the same version, one succeeds and one fails with a conflict; nothing is lost | all |
| C6 | Every change appears in history with author and session; one step restores the previous version | all |
| C7 | A session assigned tenant A cannot read or write tenant B | 3 |
| C8 | The dreaming role can create proposals but cannot write memory | 2, 3 |
| C9 | A planted secret never appears in a stored proposal, its diff, or a stored pending write | 2, 3 |
| C10 | A proposal adding or removing commands, URLs, credentials, scope or permissions, or containing instruction-like text (including in its description), is flagged | 2, 3 |
| C11 | Pending entries are never returned as curated memory, and memory text cannot break out of its context wrapper | all |
| C12 | The owner promotes exactly what they reviewed: a later change to the pending entry or the curated base causes a conflict | all |

Agent behaviour (reading the index first, opening files on demand) is checked by review, not by the suite.

---

## Changelog

- **0.1.0**: initial draft.
