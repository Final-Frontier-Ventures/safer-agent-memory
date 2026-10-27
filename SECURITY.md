# Security policy

AMS exists to make agent memory harder to abuse, so bypasses are the most valuable reports we can get.

## In scope

- Any way for an agent (or content it reads) to write `active` memory above `run`, clear taint, or set provenance fields.
- Cross-tenant reads or writes through the harness or a reference backend.
- Secrets surviving redaction into a stored proposal.
- Injection payloads that reach curated memory without a review flag.
- FTS, path or SQL injection in the reference implementation.

Weaknesses in the regex-based redaction and flagging are known limits, but new bypass classes are still welcome.

## Reporting

Email lesith@ffvlk.com with a description and a minimal reproduction (a failing conformance-style test is ideal). Please do not open a public issue for an unfixed bypass. We aim to acknowledge within 5 working days.
