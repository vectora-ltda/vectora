"""Recipe Neon — serverless Postgres gerenciado.

Neon usa um modelo branch/endpoint serverless onde cada banco tem um
endpoint que pode ser suspenso automaticamente. Para o Vectora:

    * Usar o **connection pooler** (``-pooler`` no hostname) para serverless
      e conexões curtas — evita exaurir o limite de conexões do free tier.
    * Sem ``pgbouncer=true`` na query string (Neon usa Neon Proxy, não PgBouncer).
    * SSL obrigatório (``sslmode=require``).
    * ``connect_timeout=10`` recomendado (cold-start do branch pode levar 5-10s).

Uso:
    >>> from backend.storage.recipes.neon import build_dsn, healthcheck
    >>> dsn = build_dsn(
    ...     host="ep-cool-name-123456.us-east-2.aws.neon.tech",
    ...     user="vectora",
    ...     password="senha",
    ...     database="vectora",
    ...     pooler=True,
    ... )
    >>> result = await healthcheck(dsn)
    >>> result["ok"]
    True
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Neon endpoint format: ep-<name>.<region>.aws.neon.tech
# Pooler: ep-<name>-pooler.<region>.aws.neon.tech
DSN_TEMPLATE = (
    "postgresql://{user}:{password}@{host}/{database}"
    "?sslmode=require&connect_timeout=10"
)


def build_dsn(
    *,
    host: str,
    user: str,
    password: str,
    database: str = "neondb",
    pooler: bool = True,
) -> str:
    """Monta o DSN asyncpg para Neon.

    Args:
        host:     Hostname do endpoint Neon (sem ``-pooler`` — adicionado
                  automaticamente se ``pooler=True``).
        user:     Usuário Postgres.
        password: Senha do banco (obrigatório).
        database: Nome do banco. Default ``"neondb"``.
        pooler:   Usar o connection pooler Neon? Default ``True``.

    Returns:
        DSN string pronto para ``asyncpg.create_pool()`` ou
        ``settings.postgres_dsn``.
    """
    effective_host = host
    if pooler and "-pooler." not in host:
        # Insere -pooler antes do separador de domínio
        parts = host.split(".", 1)
        if len(parts) == 2:
            effective_host = f"{parts[0]}-pooler.{parts[1]}"

    return DSN_TEMPLATE.format(
        user=user,
        password=password,
        host=effective_host,
        database=database,
    )


def configure_settings(settings: Any, **kwargs: Any) -> None:
    """Aplica flags Neon ao objeto settings.

    Define ``storage_mode = "complete"`` e ``postgres_dsn`` via ``build_dsn``.
    """
    settings.storage_mode = "complete"
    settings.postgres_dsn = build_dsn(**kwargs)
    logger.info(
        "Neon configurado: storage_mode=complete dsn=%s…",
        settings.postgres_dsn[:40],
    )


async def healthcheck(dsn: str | None = None) -> dict[str, Any]:
    """Smoke test de conectividade ao banco Neon.

    Verifica conexão, versão do Postgres e listagem de tabelas vectora_*.

    Args:
        dsn: DSN asyncpg. None usa ``settings.postgres_dsn``.

    Returns:
        ``{"ok": True, "pg_version": "...", "tables": [...]}``
        ou ``{"ok": False, "error": "..."}``
    """
    try:
        import asyncpg

        if dsn is None:
            from backend.settings import settings as _s

            dsn = _s.postgres_dsn
            if not dsn:
                return {"ok": False, "error": "postgres_dsn não configurado"}

        conn = await asyncpg.connect(dsn)
        try:
            row = await conn.fetchrow("SELECT version() AS v")
            pg_version = str(row["v"]).split(" ")[1] if row else "?"

            rows = await conn.fetch(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public'
                ORDER BY tablename
                """
            )
            tables = [r["tablename"] for r in rows]
        finally:
            await conn.close()

        return {"ok": True, "pg_version": pg_version, "tables": tables}

    except Exception as exc:
        logger.debug("Neon healthcheck falhou: %s", exc)
        return {"ok": False, "error": str(exc)}
