"""Recipe Supabase — Postgres gerenciado + pgvector.

Supabase expõe o Postgres via dois endpoints:
    * **Direct** — ``db.<project>.supabase.co:5432`` — conexões longas, SSL obrigatório.
    * **Pooler (Transaction)** — ``aws-0-<region>.pooler.supabase.com:6543`` —
      para serverless / short-lived (PgBouncer em modo transaction).

Para o Vectora em produção, usar o **Pooler** com ``?pgbouncer=true`` (desabilita
prepared statements que o PgBouncer Transaction mode não suporta).

Requisitos Supabase:
    - Extensão ``pgvector`` habilitada (SQL: ``CREATE EXTENSION IF NOT EXISTS vector``)
    - Schema ``vectora`` (ou ``public``) com as tabelas das migrations F2

Uso:
    >>> from backend.storage.recipes.supabase import build_dsn, healthcheck
    >>> dsn = build_dsn(
    ...     host="db.xxxx.supabase.co",
    ...     user="postgres",
    ...     password="sua-senha",
    ...     database="postgres",
    ...     pooler=True,
    ...     region="us-east-1",
    ... )
    >>> result = await healthcheck(dsn)
    >>> result["ok"]
    True
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# DSN template para conexão direta (SSL mode=require obrigatório no Supabase)
DSN_TEMPLATE_DIRECT = (
    "postgresql://{user}:{password}@db.{project}.supabase.co:5432/{database}"
    "?sslmode=require"
)

# DSN template para pooler PgBouncer (Transaction mode — serverless-friendly)
DSN_TEMPLATE_POOLER = (
    "postgresql://{user}.{project}:{password}"
    "@aws-0-{region}.pooler.supabase.com:6543/{database}"
    "?pgbouncer=true&sslmode=require"
)


def build_dsn(
    *,
    project: str = "",
    host: str = "",
    user: str = "postgres",
    password: str,
    database: str = "postgres",
    pooler: bool = True,
    region: str = "us-east-1",
) -> str:
    """Monta o DSN asyncpg para Supabase.

    Args:
        project:  ID do projeto Supabase (ex: ``abcdefghij``). Usado quando
                  ``host`` não é fornecido.
        host:     Hostname direto (override do template).
        user:     Usuário Postgres. Default ``"postgres"``.
        password: Senha do banco (obrigatório).
        database: Nome do banco. Default ``"postgres"``.
        pooler:   Usar PgBouncer pooler? Default ``True`` (recomendado).
        region:   Região AWS do pooler. Default ``"us-east-1"``.

    Returns:
        DSN string pronto para ``asyncpg.create_pool()`` ou
        ``settings.postgres_dsn``.
    """
    if host:
        dsn = f"postgresql://{user}:{password}@{host}/{database}?sslmode=require"
        if pooler:
            dsn += "&pgbouncer=true"
        return dsn

    if pooler:
        return DSN_TEMPLATE_POOLER.format(
            user=user,
            project=project,
            password=password,
            region=region,
            database=database,
        )

    return DSN_TEMPLATE_DIRECT.format(
        user=user,
        project=project,
        password=password,
        database=database,
    )


def configure_settings(settings: Any, **kwargs: Any) -> None:
    """Aplica flags Supabase ao objeto settings.

    Configura ``storage_mode = "complete"`` e define o ``postgres_dsn`` a
    partir dos parâmetros fornecidos.

    Args:
        settings: Instância de ``src.settings.Settings``.
        **kwargs: Mesmos kwargs de ``build_dsn()``.
    """
    settings.storage_mode = "complete"
    settings.postgres_dsn = build_dsn(**kwargs)
    logger.info(
        "Supabase configurado: storage_mode=complete dsn=%s…",
        settings.postgres_dsn[:40],
    )


async def healthcheck(dsn: str | None = None) -> dict[str, Any]:
    """Smoke test de conectividade ao Supabase Postgres.

    Testa a conexão, verifica se ``pgvector`` está instalado e se as tabelas
    do Vectora existem.

    Args:
        dsn: DSN asyncpg. None usa ``settings.postgres_dsn``.

    Returns:
        ``{"ok": True, "pgvector": True, "tables": [...]}``
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
            # Testa pgvector
            row = await conn.fetchrow(
                "SELECT COUNT(*) AS n FROM pg_extension WHERE extname = 'vector'"
            )
            pgvector = bool(row and row["n"] > 0)

            # Lista tabelas vectora_*
            rows = await conn.fetch(
                """
                SELECT tablename FROM pg_tables
                WHERE schemaname = 'public'
                  AND tablename LIKE 'vectora_%'
                  OR tablename IN ('users','refresh_tokens','audit','secrets')
                ORDER BY tablename
                """
            )
            tables = [r["tablename"] for r in rows]
        finally:
            await conn.close()

        return {"ok": True, "pgvector": pgvector, "tables": tables}

    except Exception as exc:
        logger.debug("Supabase healthcheck falhou: %s", exc)
        return {"ok": False, "error": str(exc)}
