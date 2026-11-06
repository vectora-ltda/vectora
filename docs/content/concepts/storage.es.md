---
title: "Storage: lite vs. complete"
weight: 4
---

Vectora tiene dos modos de almacenamiento, controlados por la variable `STORAGE_MODE`. La elección entre ellos es cuestión de **infraestructura, no de capacidad** — ambos modos son totalmente funcionales en producción.

## Los dos modos

|                                  | **lite** (por defecto)          | **completo**                        |
| --------------------------------- | --------------------------------- | ------------------------------------ |
| Checkpointer (estado del agente) | SQLite                            | SQLite (siempre — ver abajo)         |
| Vector store                      | LanceDB (embebido, archivo local) | Qdrant (búsqueda híbrida dense+sparse) |
| BaseStore (memoria a largo plazo) | SQLite                            | Qdrant/Postgres                      |
| Cache / KV                        | —                                  | Redis                                |
| Infraestructura externa           | ninguna                           | Postgres + Qdrant + Redis            |
| Suscripción                       | Free o Pro                        | Requiere suscripción Pro activa       |

## La regla que nunca cambia: la identidad queda en SQLite

Sin importar el `STORAGE_MODE`, **usuarios, sesiones, logs de auditoría y configuración siempre viven en SQLite** — nunca en Postgres. `storage_mode` solo afecta el checkpointer de ejecución del agente, el vector store y el caché. Esta es una garantía de producto, no un detalle de implementación: nunca pierdes acceso a tu cuenta porque una instancia de Postgres se cayó.

## Cuándo lite es suficiente (respuesta corta: casi siempre)

No hay un límite técnico documentado que obligue a migrar a completo. LanceDB es local (memory-mapped, sin servidor) y SQLite corre en modo WAL (los lectores concurrentes no bloquean al escritor). Usa lite cuando:

- Usas Vectora localmente como usuario individual.
- Quieres **cero infraestructura** — sin Docker, sin servicios externos, nada más que mantener corriendo además del propio Vectora.
- No necesitas replicación/backup gestionados por un servicio de base de datos de terceros.

## Cuándo completo tiene sentido

No se trata de "manejar más usuarios" — se trata de **durabilidad e infraestructura gestionada**:

- Ya ejecutas Postgres/Redis/Qdrant en tu empresa y quieres consolidar backup, monitoreo y alta disponibilidad en un solo lugar.
- Quieres la búsqueda vectorial híbrida nativa de Qdrant (dense + sparse) en lugar del LanceDB local.
- Quieres un caché distribuido (Redis) compartido entre múltiples instancias de Vectora detrás de un balanceador de carga.
- Tienes una suscripción Pro activa y necesitas esta topología por requisitos de infraestructura o cumplimiento interno. Alojar las bases de datos por tu cuenta no sustituye la suscripción.

## Cómo activarlo

```bash
vectora storage wizard
```

El wizard ofrece 4 caminos: Supabase, Neon, Qdrant Cloud, o Postgres autoalojado — completa `POSTGRES_DSN`, `QDRANT_URL`, `QDRANT_API_KEY` y define `STORAGE_MODE=complete` por ti.

O manualmente, vía `~/.vectora/.env`:

```env
STORAGE_MODE=complete
POSTGRES_DSN=postgresql+asyncpg://user:pass@host:5432/vectora
QDRANT_URL=https://your-cluster.qdrant.io
QDRANT_API_KEY=...
REDIS_URL=redis://user:pass@host:6379/0
```

Localmente, `scons docker` (desde la raíz del repositório, si ejecutas desde el código fuente) levanta los tres servicios vía Docker Compose para probar el modo completo sin un proveedor gestionado.

## Migrar datos entre modos

```bash
vectora storage migrate status              # lista migraciones aplicadas/pendientes
vectora storage migrate upgrade              # aplica migraciones pendientes de Postgres
vectora storage migrate to-postgres          # copia schema/datos de SQLite a Postgres
vectora storage migrate to-qdrant <collection> # migra embeddings de LanceDB a Qdrant
vectora storage migrate to-pgvector          # alternativa: LanceDB → pgvector en Postgres
vectora storage migrate memory-to-native     # memorias antiguas → store nativo
```

## Límite práctico conocido

El único número documentado en el código es el pool de conexiones de Postgres: `min_size=2, max_size=20`. Esto no es un techo de usuarios — es la cantidad de consultas concurrentes a la base de datos; más allá de eso, las solicitudes se encolan en lugar de fallar.

## Ver también

- [Configuration](../../getting-started/configuration) — dónde se define `STORAGE_MODE`
- [Deployment: Docker](../../deployment/docker) — levantando la infraestructura opcional
