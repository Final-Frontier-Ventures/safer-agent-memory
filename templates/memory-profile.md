# Memory profile: <project>

Applies: Agent Memory Standard v0.1. Confirmed by owner: <name>, <date>.

## Shape

- Agents and sessions: <names, framework or SDK>
- Where they run: <one host | several hosts | serverless>
- System of record: <database / graph / files>; domain facts stay there
- Tier: <1 | 2 | 3> because <reason>

## Scopes

- `org`: <shared across projects? where it lives>
- `project:<id>`: <id>
- `tenant:<id>`: tenant unit = <customer | engagement | account | workspace | none>
- `run:<session id>`: scratch, cleared after each dreaming pass

## Storage

- Form: <file-backed | file-backed + index | SQLite per scope | Postgres>
- Location: <path or connection>
- Permission enforcement: <deny rules | read-only mounts | RLS>

## Taint sources

| Source | Tool or code path | Wrapped with |
| --- | --- | --- |
| <web fetch> | <module.function> | `session.tainting` |

## Dreaming

- Cadence: <weekly per scope | at close-out>
- Inputs: <run notes, pending writes, transcripts at ...>
- Review surface: <ams review CLI | PR on dream/<date> branch | other>

## Retention

- Tenant memory at end of relationship: <archive | delete> after <period>
- Run memory: deleted after each dreaming pass
- Transcripts: <retention>

## Open decisions for the owner

- [ ] <decision>
