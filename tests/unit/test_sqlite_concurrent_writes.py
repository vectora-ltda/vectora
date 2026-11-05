"""D2 — carga: duas sessões/workspaces escrevendo no mesmo banco ao mesmo tempo.

Valida que ``busy_timeout`` (WAL + 30s, aplicado por todo
``AsyncConnectionPool`` — ``backend/storage/sqlite/pool.py``) absorve o caso
comum de contenção — dois pools distintos apontando pro MESMO arquivo
``.db``, escrevendo linhas simultaneamente — sem lançar "database is
locked". Escreve direto via SQL na tabela de teste (sem depender de nenhum
checkpointer/saver) — os PRAGMAs de hardening são a única coisa sob teste
aqui; a asserção sobre o valor exato dos PRAGMAs num pool isolado já vive em
``test_storage_pool.py::TestStoragePool.test_pool_lite_pragma_wal``, então
este módulo cobre especificamente o cenário de contenção entre pools
concorrentes, que aquele não exercita."""

from __future__ import annotations

import asyncio

import pytest

from backend.storage.sqlite.pool import AsyncConnectionPool

_SETUP_SQL = """
CREATE TABLE IF NOT EXISTS test_rows (
    workspace_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    PRIMARY KEY (workspace_id, seq)
);
"""


async def _open_hardened(db_path: str) -> AsyncConnectionPool:
    """Réplica do que agent_factory._ensure_infra faz: abre o pool com os
    PRAGMAs de hardening aplicados por conexão."""
    pool = AsyncConnectionPool(db_path, min_size=1, max_size=4)
    await pool.open()
    async with pool.acquire() as conn:
        await conn.execute(_SETUP_SQL)
        await conn.commit()
    return pool


async def _write_rows(pool: AsyncConnectionPool, workspace_id: str, n: int) -> None:
    for i in range(n):
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO test_rows (workspace_id, seq) VALUES (?, ?)",
                (workspace_id, i),
            )
            await conn.commit()


async def _upsert_rows(pool: AsyncConnectionPool, workspace_id: str, n: int) -> None:
    for i in range(n):
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO test_rows (workspace_id, seq) VALUES (?, ?) "
                "ON CONFLICT (workspace_id, seq) DO UPDATE SET seq = excluded.seq",
                (workspace_id, i),
            )
            await conn.commit()


@pytest.mark.asyncio
async def test_two_concurrent_sessions_write_same_db_without_locking_errors(tmp_path):
    """2 'workspaces' (pools separados) gravando no mesmo banco."""
    db_path = str(tmp_path / "shared.db")

    pool_a = await _open_hardened(db_path)
    pool_b = await _open_hardened(db_path)

    try:
        await asyncio.gather(
            _write_rows(pool_a, "workspace-a", 20),
            _write_rows(pool_b, "workspace-b", 20),
        )
    finally:
        await pool_a.close()
        await pool_b.close()

    # Confirma que ambas as threads persistiram — sem escrita perdida por
    # contenção silenciosa.
    pool_c = await _open_hardened(db_path)
    try:
        async with pool_c.acquire() as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM test_rows WHERE workspace_id = ?",
                ("workspace-a",),
            )
            row_a = await cur.fetchone()
            assert row_a is not None
            count_a = row_a[0]
            cur = await conn.execute(
                "SELECT COUNT(*) FROM test_rows WHERE workspace_id = ?",
                ("workspace-b",),
            )
            row_b = await cur.fetchone()
            assert row_b is not None
            count_b = row_b[0]
    finally:
        await pool_c.close()

    assert count_a == 20
    assert count_b == 20


@pytest.mark.asyncio
async def test_hardened_connection_has_busy_timeout_and_wal_under_contention(tmp_path):
    """Confirma os PRAGMAs de fato aplicados em cada pool concorrente — não só
    inferidos por ausência de "database is locked" no teste acima."""
    db_path = str(tmp_path / "shared.db")
    pool_a = await _open_hardened(db_path)
    pool_b = await _open_hardened(db_path)
    try:
        for pool in (pool_a, pool_b):
            async with pool.acquire() as conn:
                cur = await conn.execute("PRAGMA busy_timeout")
                row = await cur.fetchone()
                assert row is not None
                assert row[0] == 30000

                cur = await conn.execute("PRAGMA journal_mode")
                row = await cur.fetchone()
                assert row is not None
                assert row[0].lower() == "wal"
    finally:
        await pool_a.close()
        await pool_b.close()


@pytest.mark.asyncio
async def test_three_concurrent_sessions_same_workspace_no_exception(tmp_path):
    """Edge — 3 pools concorrentes fazendo upsert nas MESMAS linhas (pior caso
    de contenção: mesma chave primária disputada pelos 3, não só o mesmo
    arquivo)."""
    db_path = str(tmp_path / "shared.db")
    pools = [await _open_hardened(db_path) for _ in range(3)]
    try:
        await asyncio.gather(
            *(_upsert_rows(pool, "shared-workspace", 10) for pool in pools)
        )
    finally:
        for pool in pools:
            await pool.close()

    pool_check = await _open_hardened(db_path)
    try:
        async with pool_check.acquire() as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM test_rows WHERE workspace_id = ?",
                ("shared-workspace",),
            )
            row = await cur.fetchone()
            assert row is not None
            total = row[0]
    finally:
        await pool_check.close()

    assert total == 10
