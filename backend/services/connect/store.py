"""Persistência do mapeamento (plataforma, usuário externo) -> thread.

Sem isto cada mensagem recebida abriria uma conversa nova e o interlocutor
perderia todo o histórico a cada turno. A tabela vive no mesmo `backend.db`
das demais tabelas de app (ver `storage/migrations/sqlite/schema.sql`).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS connect_thread_mappings (
    platform         TEXT NOT NULL,
    platform_user_id TEXT NOT NULL,
    thread_id        TEXT NOT NULL,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (platform, platform_user_id)
)
"""


async def _get_db() -> Any:
    """Mesma conexão/`row_factory` de `scheduling/background_tasks.py` — a
    tabela mora no mesmo arquivo SQLite."""
    import aiosqlite

    from backend.settings import settings

    db_path = settings.db_dsn or ":memory:"
    conn: Any = await aiosqlite.connect(db_path)
    conn.row_factory = lambda c, r: dict(
        zip([col[0] for col in c.description], r, strict=False)
    )
    await conn.execute(_CREATE_TABLE)
    await conn.commit()
    return conn


async def lookup_thread(platform: str, platform_user_id: str) -> str | None:
    """`None` quando é a primeira mensagem dessa pessoa nessa plataforma."""
    conn = await _get_db()
    try:
        cursor = await conn.execute(
            "SELECT thread_id FROM connect_thread_mappings "
            "WHERE platform = ? AND platform_user_id = ?",
            (platform, platform_user_id),
        )
        row = await cursor.fetchone()
        return row["thread_id"] if row else None
    finally:
        await conn.close()


async def create_thread_mapping(platform: str, platform_user_id: str) -> str:
    """Cria o thread_id e grava o mapeamento.

    `INSERT OR IGNORE` + releitura: duas mensagens quase simultâneas do mesmo
    interlocutor não podem virar dois threads — quem perder a corrida reusa o
    thread de quem gravou primeiro.
    """
    thread_id = f"connect-{platform}-{uuid.uuid4().hex[:12]}"
    conn = await _get_db()
    try:
        await conn.execute(
            "INSERT OR IGNORE INTO connect_thread_mappings "
            "(platform, platform_user_id, thread_id) VALUES (?, ?, ?)",
            (platform, platform_user_id, thread_id),
        )
        await conn.commit()
        cursor = await conn.execute(
            "SELECT thread_id FROM connect_thread_mappings "
            "WHERE platform = ? AND platform_user_id = ?",
            (platform, platform_user_id),
        )
        row = await cursor.fetchone()
        return row["thread_id"] if row else thread_id
    finally:
        await conn.close()
