---
title: Docker (Optional Infrastructure)
weight: 2
---

Vectora **doesn't run as a container** — the backend runs on the host (native binary or `uv run vectora start`). The project's Docker Compose only spins up the optional infrastructure for **complete** mode: PostgreSQL, Redis, and Qdrant.

## Spinning up the infrastructure

```bash
vectora config docker up
```

Or, from source (repository root):

```bash
scons docker
```

This brings up:

| Service    | Image                      | Default port |
| ---------- | -------------------------- | ------------ |
| PostgreSQL | `pgvector/pgvector:pg16`   | 5432         |
| Redis      | `redis/redis-stack-server` | 6379         |
| Qdrant     | `qdrant/qdrant`            | 6333         |

## After it's up

Configure `~/.vectora/.env`:

```env
STORAGE_MODE=complete
POSTGRES_DSN=postgresql+asyncpg://vectora:vectora@127.0.0.1:5432/vectora
QDRANT_URL=http://127.0.0.1:6333
REDIS_URL=redis://127.0.0.1:6379/0
```

Then run `vectora start` normally — the backend detects `STORAGE_MODE=complete` and connects to these services.

## Why Redis Stack, not plain Redis

The `redis-stack-server` image includes the RediSearch and RedisJSON modules, used by the distributed LLM cache. Without those modules, Vectora automatically falls back to a local in-memory cache (nothing breaks, you just lose the benefit of sharing cache across instances).

## See also

- [Storage: lite vs. complete](../../concepts/storage) — when complete is actually worth it
- [Requirements](../requirements)
