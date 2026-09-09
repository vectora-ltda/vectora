"""Runner de schema para SQLite — arquivo único, reaplicado inteiro quando muda.

``sqlite/schema.sql`` é a fonte única de verdade do schema SQLite — não há
mais migrations numeradas (``0001_*.sql``, ``0002_*.sql``...). O runner
guarda o SHA-256 do arquivo numa tabela de controle de uma linha só; quando
o checksum muda (o arquivo foi editado), o script inteiro é reexecutado.

Todo statement do schema.sql precisa ser idempotente:
  - ``CREATE TABLE/INDEX IF NOT EXISTS`` — no-op se já existir.
  - ``INSERT ... OR IGNORE`` / ``ON CONFLICT DO UPDATE`` — seeds seguros.
  - ``ALTER TABLE t ADD COLUMN c ...`` — SQLite não tem ``ADD COLUMN IF NOT
    EXISTS``; o runner verifica ``PRAGMA table_info(t)`` antes de cada ALTER
    e pula se a coluna já existir. É assim que uma coluna nova adicionada a
    uma tabela existente chega em bancos já populados sem apagar dado.

Uso (código):
    runner = MigrationRunner(conn)
    status = await runner.status()
    await runner.apply()   # reaplica se o schema.sql mudou; no-op senão

CLI:
    vectora storage migrate status
    vectora storage migrate upgrade
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: Tentativas e intervalo pra ler schema.sql sob `PermissionError` — cobre
#: lock transitório de AV/indexador escaneando o arquivo recém-extraído
#: pelo instalador (comum logo após instalação no Windows). Não mascara
#: uma permissão genuinamente permanente: esgotadas as tentativas, o
#: `PermissionError` original propaga.
_READ_SCHEMA_RETRIES = 3
_READ_SCHEMA_RETRY_DELAY_S = 0.2

_CONTROL_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    checksum   TEXT    NOT NULL,
    applied_at TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_migration_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    checksum   TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
"""

_ALTER_ADD_COLUMN_RE = re.compile(
    r"^\s*ALTER\s+TABLE\s+(\S+)\s+ADD\s+COLUMN\s+(\S+)", re.IGNORECASE
)


@dataclass
class MigrationStatus:
    """Status do schema.sql em relação ao banco.

    Attributes:
        applied:     True se o schema já foi aplicado alguma vez.
        applied_at:  ISO timestamp da última aplicação (None se nunca aplicado).
        drift:       True se o arquivo mudou desde a última aplicação (pendente).
        checksum:    SHA-256 atual do arquivo.
    """

    applied: bool
    applied_at: str | None
    drift: bool
    checksum: str


def _split_statements(sql: str) -> list[str]:
    """Divide o script em statements individuais, descartando comentários
    de linha inteira (``-- ...``) e trechos vazios."""
    lines = [line for line in sql.split("\n") if not line.strip().startswith("--")]
    without_comments = "\n".join(lines)
    return [s.strip() for s in without_comments.split(";") if s.strip()]


class MigrationRunner:
    """Aplica o schema SQLite único em um banco, reaplicando quando muda.

    Args:
        conn:        Conexão ``aiosqlite.Connection`` aberta.
        schema_file: Caminho do ``schema.sql``. Default: ``sqlite/schema.sql``
                     ao lado deste arquivo.
    """

    def __init__(
        self,
        conn: Any,  # aiosqlite.Connection
        schema_file: str | Path | None = None,
    ) -> None:
        self._conn = conn
        self._file = (
            Path(schema_file)
            if schema_file
            else Path(__file__).parent / "sqlite" / "schema.sql"
        )

    async def _ensure_control_table(self) -> None:
        await self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migration_history ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, checksum TEXT NOT NULL, "
            "applied_at TEXT NOT NULL)"
        )
        await self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "id INTEGER PRIMARY KEY CHECK (id = 1), checksum TEXT NOT NULL, "
            "applied_at TEXT NOT NULL)"
        )
        # Compat: bancos que já rodaram o sistema de migrations antigo
        # (versionado, NNNN_nome.sql) têm schema_migrations no formato
        # (version, name, applied_at, checksum) — sem coluna `id`. O CREATE
        # TABLE IF NOT EXISTS acima é no-op nesse caso; sem este check, toda
        # leitura subsequente quebra com "no such column: id". A tabela é só
        # bookkeeping (não guarda dado de usuário) — seguro recriar vazia, o
        # schema.sql novo é idempotente e reaplica tudo do jeito certo.
        cursor = await self._conn.execute("PRAGMA table_info(schema_migrations)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "id" not in cols:
            await self._conn.execute(
                "INSERT INTO schema_migration_history (checksum, applied_at) "
                "SELECT old.checksum, old.applied_at FROM schema_migrations old "
                "WHERE NOT EXISTS (SELECT 1 FROM schema_migration_history h "
                "WHERE h.checksum = old.checksum AND h.applied_at = old.applied_at)"
            )
            await self._conn.executescript(
                "DROP TABLE schema_migrations;" + _CONTROL_SCHEMA
            )
        await self._conn.commit()

    async def _stored_readonly(self) -> dict[str, str] | None:
        """Read the control row without creating tables or committing."""
        cursor = await self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'schema_migrations'"
        )
        if await cursor.fetchone() is None:
            return None
        cursor = await self._conn.execute("PRAGMA table_info(schema_migrations)")
        cols = {row[1] for row in await cursor.fetchall()}
        if "id" not in cols or "checksum" not in cols or "applied_at" not in cols:
            return None
        cursor = await self._conn.execute(
            "SELECT checksum, applied_at FROM schema_migrations WHERE id = 1"
        )
        row = await cursor.fetchone()
        return None if row is None else {"checksum": row[0], "applied_at": row[1]}

    async def _read_schema(self) -> tuple[str, str]:
        """Retorna ``(conteúdo, checksum)`` do schema.sql.

        Retry com backoff curto sob ``PermissionError`` — lock transitório
        (AV/indexador) no arquivo recém-extraído pelo instalador some
        sozinho em milissegundos; sem retry, essa janela vira uma falha de
        boot inteira (schema nunca aplicado nessa sessão).
        """
        for attempt in range(1, _READ_SCHEMA_RETRIES + 1):
            try:
                content = self._file.read_text(encoding="utf-8")
                checksum = hashlib.sha256(content.encode()).hexdigest()
                return content, checksum
            except PermissionError:
                if attempt == _READ_SCHEMA_RETRIES:
                    raise
                logger.warning(
                    "storage/migrations: PermissionError lendo %s "
                    "(tentativa %d/%d) — retentando em %.1fs",
                    self._file,
                    attempt,
                    _READ_SCHEMA_RETRIES,
                    _READ_SCHEMA_RETRY_DELAY_S,
                )
                await asyncio.sleep(_READ_SCHEMA_RETRY_DELAY_S)
        raise AssertionError("unreachable")  # loop sempre retorna ou raise

    async def _stored(self) -> dict[str, str] | None:
        await self._ensure_control_table()
        cursor = await self._conn.execute(
            "SELECT checksum, applied_at FROM schema_migrations WHERE id = 1"
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return {"checksum": row[0], "applied_at": row[1]}

    async def status(self) -> MigrationStatus:
        """Retorna o status do schema.sql em relação ao banco."""
        _content, checksum = await self._read_schema()
        stored = await self._stored_readonly()
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

    async def history(self) -> list[dict[str, str]]:
        """Return the immutable checksum history for diagnostics and support."""
        cursor = await self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'schema_migration_history'"
        )
        if await cursor.fetchone() is None:
            return []
        cursor = await self._conn.execute("PRAGMA table_info(schema_migration_history)")
        columns = {row[1] for row in await cursor.fetchall()}
        if {"checksum", "applied_at"} - columns:
            return []
        cursor = await self._conn.execute(
            "SELECT checksum, applied_at FROM schema_migration_history ORDER BY id"
        )
        rows = await cursor.fetchall()
        return [{"checksum": row[0], "applied_at": row[1]} for row in rows]

    async def plan(self) -> dict[str, Any]:
        """Describe the pending schema change without mutating the database.

        The plan is intentionally data-free: it exposes the checksum and
        statement count so operators can review drift before running an
        upgrade, while keeping migration SQL out of logs and CLI output.
        """
        content, checksum = await self._read_schema()
        status = await self.status()
        statements = _split_statements(content)
        return {
            "checksum": checksum,
            "applied": status.applied,
            "drift": status.drift,
            "statement_count": len(statements),
            "will_apply": not status.applied or status.drift,
        }

    async def _existing_columns(self, table: str) -> set[str]:
        cursor = await self._conn.execute(f"PRAGMA table_info({table})")  # nosec B608 — table vem de regex sobre schema.sql versionado, não input externo
        rows = await cursor.fetchall()
        return {row[1] for row in rows}

    async def _execute_statement(self, statement: str) -> None:
        alter_match = _ALTER_ADD_COLUMN_RE.match(statement)
        if alter_match:
            table, column = alter_match.group(1), alter_match.group(2)
            existing = await self._existing_columns(table)
            if column in existing:
                logger.debug(
                    "storage/migrations: coluna %s.%s já existe, pulando ALTER",
                    table,
                    column,
                )
                return
        await self._conn.execute(statement)

    async def apply(self) -> bool:
        """Reaplica o schema.sql inteiro se ele mudou desde a última vez.

        Returns:
            True se o schema foi (re)aplicado nesta chamada, False se já
            estava atualizado.
        """
        content, checksum = await self._read_schema()
        stored = await self._stored()
        if stored is not None and stored["checksum"] == checksum:
            logger.info("storage/migrations: schema já atualizado — nada a fazer")
            return False

        logger.info("storage/migrations: aplicando schema.sql (checksum mudou)")
        for statement in _split_statements(content):
            await self._execute_statement(statement)
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            "INSERT INTO schema_migrations (id, checksum, applied_at) VALUES (1, ?, ?) "
            "ON CONFLICT (id) DO UPDATE SET checksum = excluded.checksum, "
            "applied_at = excluded.applied_at",
            (checksum, now),
        )
        await self._conn.execute(
            "INSERT INTO schema_migration_history (checksum, applied_at) VALUES (?, ?)",
            (checksum, now),
        )
        await self._conn.commit()
        logger.info("storage/migrations: schema.sql aplicado")
        return True

    # Alias retrocompatível — mesma semântica de apply(), mantido porque o
    # nome "upgrade" já é o vocabulário usado pelo CLI (`vectora storage
    # migrate upgrade`).
    async def upgrade(self) -> bool:
        return await self.apply()


# ---------------------------------------------------------------------------
# Atalho de conveniência
# ---------------------------------------------------------------------------


async def run_migrations(
    conn: Any,
    schema_file: str | Path | None = None,
) -> bool:
    """Reaplica o schema.sql em ``conn`` se ele mudou desde a última vez.

    Atalho para uso no startup do servidor:

        from backend.storage.migrations import run_migrations
        await run_migrations(conn)

    Returns:
        True se o schema foi (re)aplicado nesta chamada.
    """
    runner = MigrationRunner(conn, schema_file)
    return await runner.apply()
