"""Runner de schema para PostgreSQL (asyncpg) — arquivo único, reaplicado
inteiro quando muda.

Mesmo contrato do runner SQLite (runner.py), mas mais simples: Postgres
suporta ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS`` nativamente, então o
runner não precisa checar colunas existentes antes de cada ALTER — o
schema.sql inteiro é reexecutado de uma vez via ``asyncpg``.

Uso:
    async with pool.acquire() as conn:
        runner = PostgresMigrationRunner(conn)
        await runner.apply()
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONTROL_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    id         INTEGER     PRIMARY KEY CHECK (id = 1),
    checksum   TEXT        NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


@dataclass
class MigrationStatus:
    applied: bool
    applied_at: str | None
    drift: bool
    checksum: str


class PostgresMigrationRunner:
    """Aplica o schema Postgres único em uma conexão asyncpg.

    Args:
        conn:        asyncpg.Connection aberta.
        schema_file: Caminho do ``schema.sql``. Default: ``postgres/schema.sql``
                     ao lado deste arquivo.
    """

    def __init__(
        self,
        conn: Any,
        schema_file: str | Path | None = None,
    ) -> None:
        self._conn = conn
        self._file = (
            Path(schema_file)
            if schema_file
            else Path(__file__).parent / "postgres" / "schema.sql"
        )

    async def _ensure_control_table(self) -> None:
        await self._conn.execute(_CONTROL_SCHEMA)
        # Compat: bancos que já rodaram o sistema de migrations antigo
        # (versionado) têm schema_migrations no formato (version, name,
        # applied_at, checksum) — sem coluna `id`. O CREATE TABLE IF NOT
        # EXISTS acima é no-op nesse caso; sem este check, toda leitura
        # subsequente quebra com "column id does not exist". A tabela é só
        # bookkeeping (não guarda dado de usuário) — seguro recriar vazia.
        row = await self._conn.fetchrow(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'schema_migrations' AND column_name = 'id'"
        )
        if row is None:
            await self._conn.execute("DROP TABLE schema_migrations")
            await self._conn.execute(_CONTROL_SCHEMA)

    def _read_schema(self) -> tuple[str, str]:
        content = self._file.read_text(encoding="utf-8")
        checksum = hashlib.sha256(content.encode()).hexdigest()
        return content, checksum

    async def _stored(self) -> dict[str, str] | None:
        await self._ensure_control_table()
        row = await self._conn.fetchrow(
            "SELECT checksum, applied_at::text AS applied_at "
            "FROM schema_migrations WHERE id = 1"
        )
        if row is None:
            return None
        return {"checksum": row["checksum"], "applied_at": row["applied_at"]}

    async def status(self) -> MigrationStatus:
        """Retorna o status do schema.sql em relação ao banco."""
        _content, checksum = self._read_schema()
        stored = await self._stored()
        if stored is None:
            return MigrationStatus(
                applied=False, applied_at=None, drift=False, checksum=checksum
            )
        return MigrationStatus(
            applied=True,
            applied_at=stored["applied_at"],
            drift=stored["checksum"] != checksum,
            checksum=checksum,
        )

    async def apply(self) -> bool:
        """Reaplica o schema.sql inteiro se ele mudou desde a última vez.

        Returns:
            True se o schema foi (re)aplicado nesta chamada, False se já
            estava atualizado.
        """
        content, checksum = self._read_schema()
        stored = await self._stored()
        if stored is not None and stored["checksum"] == checksum:
            logger.debug(
                "storage/migrations/postgres: schema já atualizado — nada a fazer"
            )
            return False

        logger.info(
            "storage/migrations/postgres: aplicando schema.sql (checksum mudou)"
        )
        await self._conn.execute(content)
        now = datetime.now(UTC)
        await self._conn.execute(
            "INSERT INTO schema_migrations (id, checksum, applied_at) VALUES (1, $1, $2) "
            "ON CONFLICT (id) DO UPDATE SET checksum = EXCLUDED.checksum, "
            "applied_at = EXCLUDED.applied_at",
            checksum,
            now,
        )
        logger.info("storage/migrations/postgres: schema.sql aplicado")
        return True

    async def upgrade(self) -> bool:
        return await self.apply()
