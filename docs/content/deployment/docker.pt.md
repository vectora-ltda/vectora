---
title: Docker (Infraestrutura Opcional)
weight: 2
---

O Vectora **não roda como container** — o backend roda no host (binário nativo ou `uv run vectora start`). O Docker Compose do projeto sobe só a infraestrutura opcional do modo **complete**: PostgreSQL, Redis e Qdrant.

## Subir a infraestrutura

```bash
vectora config docker up
```

Ou, a partir do código-fonte (raiz do repositório):

```bash
scons docker
```

Isso sobe:

| Serviço    | Imagem                     | Porta padrão |
| ---------- | -------------------------- | ------------ |
| PostgreSQL | `pgvector/pgvector:pg16`   | 5432         |
| Redis      | `redis/redis-stack-server` | 6379         |
| Qdrant     | `qdrant/qdrant`            | 6333         |

## Depois de subir

Configure `~/.vectora/.env`:

```env
STORAGE_MODE=complete
POSTGRES_DSN=postgresql+asyncpg://vectora:vectora@127.0.0.1:5432/vectora
QDRANT_URL=http://127.0.0.1:6333
REDIS_URL=redis://127.0.0.1:6379/0
```

E rode `vectora start` normalmente — o backend detecta o `STORAGE_MODE=complete` e conecta nesses serviços.

## Por que Redis Stack, não Redis puro

A imagem `redis-stack-server` inclui os módulos RediSearch e RedisJSON, usados pelo cache distribuído de LLM. Sem esses módulos, o Vectora cai automaticamente pra um cache em memória local (sem quebrar, só sem o benefício de compartilhar cache entre instâncias).

## Veja também

- [Storage: lite vs. complete](../../concepts/storage) — quando complete realmente vale a pena
- [Requisitos](../requirements)
