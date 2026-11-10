---
title: Configuration
weight: 4
---

## Dónde viven los datos

Todo lo que Vectora necesita para ejecutarse vive en `~/.vectora/`:

```text
~/.vectora/
├── config.toml             # configuración de runtime (proveedores, almacenamiento)
├── auth.key                # clave JWT (autogenerada, permisos 600)
├── data/
│   ├── vectora.db          # usuarios, sesiones, memorias, checkpoints (SQLite WAL)
│   ├── embedding_queue.db  # cola de indexación asíncrona
│   ├── traces.db           # spans de observabilidad
│   └── lancedb/            # vector store (modo lite)
├── artifacts/              # planes/specs generados por el agente
├── secrets/
│   ├── system.kdbx         # bóveda de secretos del sistema (KeePassXC)
│   └── users/{id}.kdbx     # bóveda por usuario (API keys, SSH)
├── skills/{user_id}/       # skills instaladas (SKILL.md)
├── safe_roots.json         # rutas confiables configuradas por el admin
└── workspaces.json         # workspaces registrados
```

## Jerarquía de variables de entorno

Tres capas, la más específica gana:

```text
defaults.env (integrado en la app)  →  .env (proyecto, dev)  →  ~/.vectora/.env (usuario, global)
```

Las sobreescrituras por usuario (ej.: una API key distinta para un miembro del equipo) viven en la columna `env_overrides` de la base de datos, editable vía **Configuración → Entorno → Envs**.

## Modo de almacenamiento: lite o completo

Controlado por `STORAGE_MODE` (`lite` por defecto). Consulta la guía dedicada en [Almacenamiento: lite vs. completo](../../concepts/storage) para saber cuándo tiene sentido cada uno — resumen rápido: **lite** (SQLite + LanceDB) no requiere infra y maneja escala real; **completo** (Postgres + Qdrant + Redis) es cuestión de durabilidad/infra gestionada, no una barrera de rendimiento. Sin importar el modo, usuarios/auth/config siempre quedan en SQLite.

## Categorías de configuración

La configuración se organiza por alcance y capacidad. **Preferencias** contiene opciones personales; **Entorno** contiene proveedores de IA, enrutamiento, variables, skills, plugins MCP e integraciones; **Administración** contiene controles de la instancia. La disponibilidad depende del rol del usuario. Consulta [Usando la configuración](../../guides/using-settings) para más detalles.
## Siguiente paso

→ [Primer workspace](../first-workspace)
