import asyncio
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
