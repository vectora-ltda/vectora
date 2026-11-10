---
title: "Storage: lite vs. complete"
weight: 4
---

Vectora has two storage modes, controlled by the `STORAGE_MODE` variable. The choice between them is about **infrastructure, not capability** — both modes are fully production-functional.

## The two modes

|                              | **lite** (default)             | **complete**                        |
| ---------------------------- | ------------------------------ | ----------------------------------- |
| Checkpointer (agent state)   | SQLite                         | SQLite (always — see below)         |
| Vector store                 | LanceDB (embedded, local file) | Qdrant (hybrid dense+sparse search) |
| BaseStore (long-term memory) | SQLite                         | Qdrant/Postgres                     |
| Cache / KV                   | —                              | Redis                               |
| External infrastructure      | none                           | Postgres + Qdrant + Redis           |
| Entitlement                  | Free or Pro                    | Requires an active Pro entitlement  |

## The rule that never changes: identity stays in SQLite

Regardless of `STORAGE_MODE`, **users, sessions, audit logs, and settings always live in SQLite** — never in Postgres. `storage_mode` only affects the agent's execution checkpointer, the vector store, and the cache. This is a product guarantee, not an implementation detail: you never lose access to your account because a Postgres instance went down.

## When lite is enough (short answer: almost always)

There's no documented technical limit forcing a migration to complete. LanceDB is local (memory-mapped, no server) and SQLite runs in WAL mode (concurrent readers don't block the writer). Use lite when:

- You're using Vectora locally as an individual.
- You want **zero infrastructure** — no Docker, no external services, nothing else to keep running besides Vectora itself.
- You don't need replication/backup managed by a third-party database service.

## When complete makes sense

It's not about "handling more users" — it's about **durability and managed infrastructure**:

- You already run Postgres/Redis/Qdrant at your company and want to consolidate backup, monitoring, and high availability in one place.
- You want Qdrant's native hybrid vector search (dense + sparse) instead of local LanceDB.
- You want a distributed cache (Redis) shared across multiple Vectora instances behind a load balancer.
- You have an active Pro entitlement and need this topology for infrastructure or internal compliance requirements. Self-hosting the databases does not replace the entitlement.

## How to enable it

```bash
vectora storage wizard
```

The wizard offers 4 paths: Supabase, Neon, Qdrant Cloud, or self-hosted Postgres — it fills in `POSTGRES_DSN`, `QDRANT_URL`, `QDRANT_API_KEY`, and sets `STORAGE_MODE=complete` for you.

Or manually, via `~/.vectora/.env`:

```env
STORAGE_MODE=complete
POSTGRES_DSN=postgresql+asyncpg://user:pass@host:5432/vectora
QDRANT_URL=https://your-cluster.qdrant.io
QDRANT_API_KEY=...
REDIS_URL=redis://user:pass@host:6379/0
```

Locally, `scons docker` (from the repository root, if running from source) spins up all three services via Docker Compose to test complete mode without a managed provider.

## Migrating data between modes

```bash
vectora storage migrate status              # lists applied/pending migrations
vectora storage migrate upgrade              # applies pending Postgres migrations
vectora storage migrate to-postgres          # copies schema/data from SQLite to Postgres
vectora storage migrate to-qdrant <collection> # migrates embeddings from LanceDB to Qdrant
vectora storage migrate to-pgvector          # alternative: LanceDB → pgvector on Postgres
vectora storage migrate memory-to-native     # old memories → native store
```

## Known practical limit

The only documented number in the code is the Postgres connection pool: `min_size=2, max_size=20`. This isn't a user ceiling — it's the number of concurrent database queries; beyond that, requests queue instead of failing.

## See also

- [Configuration](../../getting-started/configuration) — where `STORAGE_MODE` is set
- [Deployment: Docker](../../deployment/docker) — spinning up the optional infrastructure
