# Changelog

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
