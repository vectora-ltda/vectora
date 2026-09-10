import asyncio
import os
import sqlite3
from pathlib import Path

import pytest

from backend.services.media_quota import MediaQuota


def _quota(path: Path) -> MediaQuota:
    schema = (
        Path(__file__).parents[2]
        / "backend"
        / "storage"
        / "migrations"
        / "sqlite"
        / "schema.sql"
    )
    with sqlite3.connect(path) as connection:
        connection.executescript(schema.read_text(encoding="utf-8"))
    return MediaQuota(path)


@pytest.mark.asyncio
async def test_reserve_is_idempotent_and_summary_is_durable(tmp_path) -> None:
    quota = _quota(tmp_path / "quota.sqlite3")

    first = await quota.reserve(
        user_id="u1", operation="generate_image", idempotency_key="call-1"
    )
    second = await quota.reserve(
        user_id="u1", operation="generate_image", idempotency_key="call-1"
    )

    assert first == second
    assert (await quota.summary("u1"))["used"] == 1


@pytest.mark.asyncio
async def test_reserve_rejects_when_monthly_limit_is_exceeded(tmp_path) -> None:
    quota = _quota(tmp_path / "quota.sqlite3")
    for index in range(10):
        assert await quota.reserve(
            user_id="u1",
            operation="generate_image",
            idempotency_key=f"call-{index}",
        )

    assert (
        await quota.reserve(
            user_id="u1",
            operation="generate_image",
            idempotency_key="call-over-limit",
        )
        is None
    )


@pytest.mark.asyncio
async def test_reservas_concorrentes_nao_ultrapassam_o_limite(tmp_path) -> None:
    quota = _quota(tmp_path / "quota.sqlite3")

    results = await asyncio.gather(
        *(
            quota.reserve(
                user_id="u1",
                operation="generate_image",
                idempotency_key=f"parallel-{index}",
            )
            for index in range(12)
        )
    )

    assert sum(result is not None for result in results) == 10
    assert (await quota.summary("u1"))["used"] == 10


@pytest.mark.asyncio
async def test_retry_de_reserva_falha_reusa_a_mesma_debitacao(tmp_path) -> None:
    quota = _quota(tmp_path / "quota.sqlite3")
    first = await quota.reserve(
        user_id="u1", operation="generate_image", idempotency_key="retry-1"
    )
    assert first is not None
    await quota.finalize(first, state="failed")

    retry = await quota.reserve(
        user_id="u1", operation="generate_image", idempotency_key="retry-1"
    )
    assert retry == first
    assert (await quota.summary("u1"))["used"] == 1


@pytest.mark.asyncio
async def test_reserva_finalizada_preserva_estado_e_nao_reautoriza_operacao(
    tmp_path,
) -> None:
    """Uma chave já finalizada não volta ao estado reservado em novo pedido."""
    quota = _quota(tmp_path / "quota.sqlite3")
    first = await quota.reserve(
        user_id="u1", operation="generate_image", idempotency_key="finalized-1"
    )
    assert first is not None
    await quota.finalize(first, state="finalized")

    repeated = await quota.reserve(
        user_id="u1", operation="generate_image", idempotency_key="finalized-1"
    )

    assert repeated is not None
    assert repeated.state == "finalized"
    assert (await quota.summary("u1"))["used"] == 1


@pytest.mark.asyncio
async def test_tier_indisponivel_bloqueia_reserva_de_usuario_autenticado(
    tmp_path, monkeypatch
) -> None:
    quota = _quota(tmp_path / "quota.sqlite3")
    monkeypatch.setattr(
        "backend.rbac.subscription.get_current_tier",
        lambda _user_id: None,
    )
    assert (
        await quota.reserve(
            user_id="u1", operation="generate_image", idempotency_key="fail-closed"
        )
        is None
    )


@pytest.mark.asyncio
async def test_summary_postgres_usa_tier_do_store_de_entitlements(
    tmp_path, monkeypatch
) -> None:
    quota = _quota(tmp_path / "quota.sqlite3")

    async def fake_pool() -> _FakePool:
        return _FakePool({"used_units": 99})

    monkeypatch.setattr(quota, "_postgres_pool", fake_pool)
    monkeypatch.setattr(quota, "_current_tier", lambda _user_id: "pro")

    result = await quota._summary_postgres("u1")

    assert result["limit"] == 100
    assert result["remaining"] == 1


@pytest.mark.asyncio
async def test_reservas_postgres_concorrentes_mesma_chave_sao_idempotentes(
    monkeypatch,
) -> None:
    """Concorrência PostgreSQL deve produzir uma reserva e um único débito."""
    dsn = os.getenv("VECTORA_TEST_POSTGRES_DSN")
    if not dsn:
        pytest.skip("VECTORA_TEST_POSTGRES_DSN não configurado")
    import asyncpg

    pool = await asyncpg.create_pool(dsn)
    quota = MediaQuota()
    user_id = f"postgres-idempotency-{os.urandom(8).hex()}"
    key = f"postgres-call-{os.urandom(8).hex()}"
    try:
        async with pool.acquire() as connection:
            await connection.execute(
                "CREATE TABLE IF NOT EXISTS media_quota_usage ("
                "user_id TEXT NOT NULL, period TEXT NOT NULL, used_units INTEGER NOT NULL DEFAULT 0, "
                "PRIMARY KEY (user_id, period))"
            )
            await connection.execute(
                "CREATE TABLE IF NOT EXISTS media_quota_reservations ("
                "id TEXT PRIMARY KEY, user_id TEXT NOT NULL, period TEXT NOT NULL, operation TEXT NOT NULL, "
                "units INTEGER NOT NULL, state TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                "UNIQUE (user_id, period, id))"
            )
        monkeypatch.setattr(quota, "_postgres_enabled", lambda: True)
        monkeypatch.setattr(quota, "_current_tier", lambda _user_id: "pro")
        monkeypatch.setattr(quota, "_postgres_pool", lambda: pool)

        results = await asyncio.gather(
            *(
                quota.reserve(
                    user_id=user_id,
                    operation="generate_image",
                    idempotency_key=key,
                )
                for _ in range(2)
            )
        )

        assert results[0] is not None
        assert results[0] == results[1]
        async with pool.acquire() as connection:
            usage = await connection.fetchval(
                "SELECT used_units FROM media_quota_usage WHERE user_id = $1",
                user_id,
            )
            reservations = await connection.fetchval(
                "SELECT COUNT(*) FROM media_quota_reservations WHERE id = $1", key
            )
        assert usage == 1
        assert reservations == 1
    finally:
        async with pool.acquire() as connection:
            await connection.execute(
                "DELETE FROM media_quota_reservations WHERE id = $1", key
            )
            await connection.execute(
                "DELETE FROM media_quota_usage WHERE user_id = $1", user_id
            )
        await pool.close()


class _FakeConnection:
    def __init__(self, row: dict[str, int]) -> None:
        self.row = row

    async def fetchrow(self, _query: str, *_args: object) -> dict[str, int]:
        return self.row


class _FakeAcquire:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self.connection

    async def __aexit__(self, *_args: object) -> None:
        return None


class _FakePool:
    def __init__(self, row: dict[str, int]) -> None:
        self.connection = _FakeConnection(row)

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.connection)
