"""Tests — storage/sqlite/pool.py (F1) e get_store (F3/F5).

Modo "lite" usa SQLite em memória; modo "complete" é skippado se postgres_dsn não
configurado, garantindo que CI verde sem infra externa.
"""

from __future__ import annotations

import pytest


@pytest.fixture(params=["lite", "complete"])
def storage_mode(request: pytest.FixtureRequest) -> str:
    return str(request.param)  # type: ignore[return-value]


@pytest.fixture(autouse=True)
def reset_storage_singletons():
    """Garante isolamento entre testes resetando singletons de factory."""
    import backend.storage.factory as _fac

    _fac._reset_singletons()
    yield
    _fac._reset_singletons()


class TestStoragePool:
    """Pool SQLite AsyncConnectionPool (F1)."""

    @pytest.mark.asyncio
    async def test_pool_lite_pragma_wal(self, tmp_path):
        """Pool abre com WAL + busy_timeout=30000 e retorna conexão funcional.

        busy_timeout explícito (não só inferido por ausência de "database is
        locked" num teste de concorrência) — consulta o PRAGMA de volta na
        conexão de verdade que o pool entrega, não uma réplica do script.
        """
        from backend.storage.sqlite.pool import AsyncConnectionPool

        db_path = str(tmp_path / "test.db")
        pool = AsyncConnectionPool(db_path, min_size=1, max_size=2)
        try:
            await pool.open()
            async with pool.acquire() as conn:
                cur = await conn.execute("PRAGMA journal_mode")
                row = await cur.fetchone()
                assert row is not None
                assert row[0].lower() == "wal"

                cur = await conn.execute("PRAGMA busy_timeout")
                row = await cur.fetchone()
                assert row is not None
                assert row[0] == 30000
        finally:
            await pool.close()

    @pytest.mark.asyncio
    async def test_pool_multiple_connections(self, tmp_path):
        """Pool libera conexões de volta ao pool após uso."""
        from backend.storage.sqlite.pool import AsyncConnectionPool

        db_path = str(tmp_path / "test.db")
        pool = AsyncConnectionPool(db_path, min_size=1, max_size=3)
        try:
            await pool.open()
            # Duas aquisições sequenciais devem funcionar sem deadlock
            for _ in range(2):
                async with pool.acquire() as conn:
                    cur = await conn.execute("SELECT 1")
                    row = await cur.fetchone()
                    assert row is not None
        finally:
            await pool.close()


class TestGetStore:
    """get_store retorna VectoraStore (nativo, aiosqlite) no modo lite."""

    @pytest.mark.asyncio
    async def test_lite_store_setup(self, tmp_path, monkeypatch):
        import backend.settings as _settings_mod
        from backend.storage.factory import get_store

        monkeypatch.setattr(_settings_mod.settings, "storage_mode", "lite")
        monkeypatch.setattr(
            _settings_mod.settings, "db_dsn", str(tmp_path / "store.db")
        )

        store = await get_store()
        assert store is not None

    @pytest.mark.asyncio
    async def test_store_singleton(self, tmp_path, monkeypatch):
        """Duas chamadas consecutivas retornam o mesmo objeto."""
        import backend.settings as _settings_mod
        from backend.storage.factory import get_store

        monkeypatch.setattr(_settings_mod.settings, "storage_mode", "lite")
        monkeypatch.setattr(
            _settings_mod.settings, "db_dsn", str(tmp_path / "store.db")
        )

        s1 = await get_store()
        s2 = await get_store()
        assert s1 is s2
