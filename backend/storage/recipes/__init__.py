"""Recipes de BaaS para o Vectora — modo complete via nuvem.

Cada módulo expõe:
    - ``DSN_TEMPLATE`` — string de conexão com placeholders ``{...}``
    - ``build_dsn(**kwargs)`` — monta o DSN a partir dos parâmetros
    - ``healthcheck()`` — smoke test de conectividade (async)
    - ``configure_settings(settings, **kwargs)`` — aplica flags específicas
      do provedor (pool sizes, SSL, schemas) ao objeto settings.

Módulos disponíveis:
    supabase     — Supabase Postgres + pgvector
    neon         — Neon serverless Postgres (pooler)
    qdrant_cloud — Qdrant Cloud (vetor + BM25 sparse)
"""

from __future__ import annotations

from backend.storage.recipes.neon import build_dsn as neon_dsn
from backend.storage.recipes.neon import healthcheck as neon_healthcheck
from backend.storage.recipes.qdrant_cloud import build_config as qdrant_cloud_config
from backend.storage.recipes.qdrant_cloud import healthcheck as qdrant_cloud_healthcheck
from backend.storage.recipes.supabase import build_dsn as supabase_dsn
from backend.storage.recipes.supabase import healthcheck as supabase_healthcheck

__all__ = [
    "neon_dsn",
    "neon_healthcheck",
    "qdrant_cloud_config",
    "qdrant_cloud_healthcheck",
    "supabase_dsn",
    "supabase_healthcheck",
]
