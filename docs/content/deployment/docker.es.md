---
title: Docker (Optional Infrastructure)
weight: 2
---

Vectora **no se ejecuta como contenedor** — el backend corre en el host (binario nativo o `uv run vectora start`). El Docker Compose del proyecto solo levanta la infraestructura opcional para el modo **completo**: PostgreSQL, Redis y Qdrant.

## Levantar la infraestructura

```bash
vectora config docker up
```

O, desde el código fuente (raíz del repositório):

```bash
scons docker
```

Esto levanta:

| Servicio   | Imagen                      | Puerto por defecto |
| ---------- | ---------------------------- | -------------------- |
| PostgreSQL | `pgvector/pgvector:pg16`     | 5432                 |
| Redis      | `redis/redis-stack-server`   | 6379                 |
| Qdrant     | `qdrant/qdrant`              | 6333                 |

## Después de que esté levantado

Configura `~/.vectora/.env`:

```env
STORAGE_MODE=complete
POSTGRES_DSN=postgresql+asyncpg://vectora:vectora@127.0.0.1:5432/vectora
QDRANT_URL=http://127.0.0.1:6333
REDIS_URL=redis://127.0.0.1:6379/0
```

Luego ejecuta `vectora start` normalmente — el backend detecta `STORAGE_MODE=complete` y se conecta a estos servicios.

## Por qué Redis Stack y no Redis simple

La imagen `redis-stack-server` incluye los módulos RediSearch y RedisJSON, usados por el caché distribuido de LLM. Sin esos módulos, Vectora cae automáticamente a un caché local en memoria (nada se rompe, solo pierdes el beneficio de compartir caché entre instancias).

## Ver también

- [Almacenamiento: lite vs. completo](../../concepts/storage) — cuándo completo realmente vale la pena
- [Requirements](../requirements)
