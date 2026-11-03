# Changelog

## Unreleased (0.2.0)

- **Postgres backend** (`sams.postgres_store.PostgresBackend`, optional extra `safer-agent-memory[postgres]`). All containers share one schema; row-level security binds each database login to one SAMS role and container through a `sams_grants` table, so the scope and status rules hold below the harness (provenance and taint are still stamped by the harness): agent logins read only `org` and their assigned container and write only `pending` rows or their run scratchpads; dreaming logins read one container and insert proposals only; owner logins can be limited to one container. Triggers assign version numbers (never restarting for a path), keep history, and make stored proposals immutable apart from a single decision. `install_schema()`, `grant()`, `revoke()` and `schema_sql()` set it up.
- The conformance suite runs against Postgres when `SAMS_TEST_POSTGRES_DSN` is set, plus Postgres-specific tests that run raw SQL as agent and dreamer logins. CI runs them on Postgres 14 and 17.
- CLI: review commands accept `--dsn` (or `SAMS_DSN`) and `--schema` for the Postgres backend.
- Conformance factories may now yield a backend and clean up afterwards.

## 0.1.1

- **Security fix (C12):** accepting a dreaming proposal now requires the fingerprint shown at review (`Owner.accept_proposal(scope, id, reviewed_digest)`; CLI `sams review accept ... --digest`). Previously a proposal rewritten in storage after review, for example by a compromised dreaming job, was promoted with its new content. New `ReviewMismatch` error and `review_digest()` helper. Reported in a comment on our launch post.
- Review now recomputes each proposal's diff and flags from the proposed content instead of trusting what the dreaming job stored, so a tampered proposal cannot hide its own flags.
- C12 conformance tests now cover a proposal changed after review.

## 0.1.0 (draft)

- Standard v0.1: scopes, status, guardrails, taint, tenant isolation, dreaming, storage forms, adoption procedure, conformance C1-C12.
- Python reference implementation (stdlib only): `AgentSession`, `DreamerSession`, `Owner`, `SQLiteBackend` (directory per scope), redaction and review flags, derived file index, `sams` CLI.
- Conformance suite and SQLite-specific defence-in-depth tests.
- Claude Code plugin with the `adopt-memory` skill; templates for Claude Code repos, Docker Compose isolation and dreaming.
- CI on Python 3.10-3.14 (Linux, macOS), package build check, PyPI trusted publishing on release, SHA-pinned actions with Dependabot.
