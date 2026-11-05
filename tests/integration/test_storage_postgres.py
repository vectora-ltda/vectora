"""Testes de integração — PostgreSQL (asyncpg + migrations + pool).

Requer Postgres rodando (vectora-postgres via docker).
Os fixtures de conftest.py sobem o container automaticamente se Docker estiver
disponível; do contrário, todos os testes são pulados.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

if TYPE_CHECKING:
    import asyncpg


async def _resolved(value: object) -> object:
    return value


class TestPostgresMigrationRunner:
    """PostgresMigrationRunner aplica e rastreia o schema Postgres único."""

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_upgrade_applies_schema_and_returns_true(self, pg_conn):
        """upgrade() aplica o schema.sql inteiro e retorna True na 1ª chamada."""
        from backend.storage.migrations.postgres_runner import PostgresMigrationRunner

        runner = PostgresMigrationRunner(pg_conn)
        applied = await runner.upgrade()
        assert applied is True

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_upgrade_idempotent(self, pg_conn):
        """Segunda chamada a upgrade() é no-op (checksum já bate) — retorna False."""
        from backend.storage.migrations.postgres_runner import PostgresMigrationRunner

        runner = PostgresMigrationRunner(pg_conn)
        await runner.upgrade()
        second = await runner.upgrade()
        assert second is False

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_schema_migrations_table_created(self, pg_conn):
        """Tabela de controle schema_migrations é criada automaticamente."""
        from backend.storage.migrations.postgres_runner import PostgresMigrationRunner

        runner = PostgresMigrationRunner(pg_conn)
        await runner.upgrade()

        row = await pg_conn.fetchrow(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = 'schema_migrations'"
        )
        assert row is not None

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_status_reflects_applied_state(self, pg_conn):
        """status() retorna um único MigrationStatus refletindo o banco."""
        from backend.storage.migrations.postgres_runner import (
            MigrationStatus,
            PostgresMigrationRunner,
        )

        runner = PostgresMigrationRunner(pg_conn)

        pending = await runner.status()
        assert isinstance(pending, MigrationStatus)
        assert pending.applied is False

        await runner.upgrade()
        applied = await runner.status()
        assert applied.applied is True
        assert applied.drift is False
        assert applied.applied_at is not None


class TestMediaQuotaPostgres:
    """Garante transições de reativação no backend PostgreSQL."""

    @pytest.mark.asyncio
    @pytest.mark.storage
    @pytest.mark.parametrize("previous_state", ["failed", "cancelled"])
    async def test_reativa_estado_e_registra_transicao(
        self,
        pg_pool: asyncpg.Pool,
        monkeypatch: pytest.MonkeyPatch,
        previous_state: str,
    ) -> None:
        from backend.services.media_quota import MediaQuota

        user_id = f"quota-{uuid4().hex}"
        key = f"retry-{uuid4().hex}"
        quota = MediaQuota()
        events: list[dict[str, object]] = []
        monkeypatch.setattr(quota, "_postgres_pool", lambda: _resolved(pg_pool))
        monkeypatch.setattr(quota, "_current_tier", lambda _user_id: "pro")
        monkeypatch.setattr(quota, "_postgres_enabled", lambda: True)
        monkeypatch.setattr(
            quota,
            "_record_transition",
            lambda **fields: events.append(fields),
        )
        async with pg_pool.acquire() as connection:
            schema = (
                Path(__file__).resolve().parents[2]
                / "backend"
                / "storage"
                / "migrations"
                / "postgres"
                / "schema.sql"
            )
            await connection.execute(schema.read_text(encoding="utf-8"))
            period = quota.period()
            await connection.execute(
                "INSERT INTO media_quota_usage VALUES ($1,$2,0)", user_id, period
            )
            await connection.execute(
                "INSERT INTO media_quota_reservations (id,user_id,period,operation,units,state) VALUES ($1,$2,$3,$4,$5,$6)",
                key,
                user_id,
                period,
                "generate_image",
                1,
                previous_state,
            )
        try:
            reservation = await quota.reserve(
                user_id=user_id, operation="generate_image", idempotency_key=key
            )
            assert reservation is not None and reservation.state == "reserved"
            assert events == [
                {
                    "operation": "generate_image",
                    "units": 1,
                    "previous_state": previous_state,
                    "new_state": "reserved",
                }
            ]
            async with pg_pool.acquire() as connection:
                usage = await connection.fetchval(
                    "SELECT used_units FROM media_quota_usage WHERE user_id = $1",
                    user_id,
                )
                assert usage == 1
        finally:
            async with pg_pool.acquire() as connection:
                await connection.execute(
                    "DELETE FROM media_quota_reservations WHERE id=$1", key
                )
                await connection.execute(
                    "DELETE FROM media_quota_usage WHERE user_id=$1", user_id
                )

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_reapply_after_content_change_updates_checksum(
        self, pg_conn, tmp_path
    ):
        """Editar o schema.sql muda o checksum: upgrade() reaplica e status() reflete."""
        from backend.storage.migrations.postgres_runner import PostgresMigrationRunner

        original = (
            Path(__file__).resolve().parents[2]
            / "backend"
            / "storage"
            / "migrations"
            / "postgres"
            / "schema.sql"
        ).read_text(encoding="utf-8")

        edited_file = tmp_path / "schema_edited.sql"
        edited_file.write_text(
            original + "\nCREATE TABLE IF NOT EXISTS _test_pg_marker (id TEXT);\n",
            encoding="utf-8",
        )

        runner = PostgresMigrationRunner(pg_conn, schema_file=edited_file)
        first = await runner.upgrade()
        assert first is True

        row = await pg_conn.fetchrow(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = '_test_pg_marker'"
        )
        assert row is not None

        status = await runner.status()
        assert status.applied is True
        assert status.drift is False

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_upgrade_from_old_versioned_control_table(self, pg_conn):
        """Banco que já rodou o sistema de migrations antigo (versionado) tem
        schema_migrations no formato (version, name, applied_at, checksum) —
        sem coluna `id`. apply() não pode quebrar com "column id does not
        exist" nesse caso; reproduz o bug real encontrado em produção."""
        from backend.storage.migrations.postgres_runner import PostgresMigrationRunner

        await pg_conn.execute("""
            CREATE TABLE schema_migrations (
                version    TEXT PRIMARY KEY,
                name       TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                checksum   TEXT NOT NULL
            )
        """)
        await pg_conn.execute(
            "INSERT INTO schema_migrations VALUES ('0001', 'sessions', now(), 'deadbeef')"
        )

        runner = PostgresMigrationRunner(pg_conn)
        applied = await runner.apply()
        assert applied is True

        status = await runner.status()
        assert status.applied is True
        assert status.drift is False


class TestPostgresPool:
    """Pool asyncpg conecta e executa queries básicas."""

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_pool_executes_select(self, pg_pool):
        async with pg_pool.acquire() as conn:
            result = await conn.fetchval("SELECT 1")
        assert result == 1

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_pool_multiple_connections(self, pg_pool):
        """Múltiplas aquisições sequenciais funcionam sem deadlock."""
        for _ in range(3):
            async with pg_pool.acquire() as conn:
                val = await conn.fetchval("SELECT 42")
                assert val == 42

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_get_pg_pool_factory(self, _storage_stack_ok, pg_dsn, monkeypatch):
        """get_pg_pool() cria pool e garante schema via migration runner."""
        if not _storage_stack_ok:
            pytest.skip("Docker indisponível — Postgres não iniciado")

        import backend.settings as _s
        import backend.storage.factory as _fac

        # Postgres é feature Pro (backend/services/subscription.py::require_pro).
        monkeypatch.setenv("VECTORA_LICENSE_BYPASS", "1")
        _fac._reset_singletons()
        monkeypatch.setattr(_s.settings, "postgres_dsn", pg_dsn)

        try:
            pool = await _fac.get_pg_pool(dsn=pg_dsn)
            assert pool is not None
            async with pool.acquire() as conn:
                val = await conn.fetchval("SELECT 1")
            assert val == 1
        finally:
            await _fac.close_pg_pool()
            _fac._reset_singletons()
            # get_pg_pool() aplica o schema.sql de verdade (commit real, fora
            # de qualquer transação de teste) — ao contrário do resto da
            # classe, que roda em cima de `pg_conn` (rollback automático via
            # conftest.py). Sem este reset, schema_migrations e as demais
            # tabelas ficam commitadas no container Postgres compartilhado e
            # quebram os testes de `TestPostgresMigrationRunner` que rodam
            # depois (assumem banco sem schema aplicado).
            import asyncpg

            reset_conn = await asyncpg.connect(pg_dsn)
            try:
                await reset_conn.execute("DROP SCHEMA public CASCADE")
                await reset_conn.execute("CREATE SCHEMA public")
            finally:
                await reset_conn.close()

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_get_pg_pool_raises_without_dsn(self, monkeypatch):
        """get_pg_pool() levanta RuntimeError quando nenhum DSN está configurado
        (com plano Pro — sem isso o gate de tier dispara antes do check de DSN,
        ver test_get_pg_pool_free_tier_raises_402_before_dsn_check abaixo)."""
        import backend.settings as _s
        import backend.storage.factory as _fac

        monkeypatch.setenv("VECTORA_LICENSE_BYPASS", "1")
        _fac._reset_singletons()
        monkeypatch.setattr(_s.settings, "postgres_dsn", None)
        monkeypatch.setattr(_s.settings, "storage_mode", "complete")

        with pytest.raises(RuntimeError, match="postgres_dsn"):
            await _fac.get_pg_pool()

        _fac._reset_singletons()

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_get_pg_pool_free_tier_raises_402_before_dsn_check(
        self, monkeypatch, tmp_path
    ) -> None:
        """Sem plano Pro, get_pg_pool() nega ANTES de checar o DSN — Postgres é
        feature de time (backend/services/subscription.py::require_pro), não
        disponível no tier free mesmo com DSN configurado."""
        import backend.settings as _s
        import backend.storage.factory as _fac
        from backend.services import license as lic

        monkeypatch.delenv("VECTORA_LICENSE_BYPASS", raising=False)
        monkeypatch.setattr(lic, "CACHE_PATH", tmp_path / "license_cache.json")
        _fac._reset_singletons()
        monkeypatch.setattr(_s.settings, "postgres_dsn", "postgresql://x/y")

        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await _fac.get_pg_pool()
        assert exc.value.status_code == 402

        _fac._reset_singletons()
