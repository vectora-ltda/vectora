"""Testes do KV distribuído (src/services/kv.py)."""

from __future__ import annotations

import asyncio

import pytest

from backend.persistence import kv as kv_mod
from backend.persistence.kv import (
    MemoryKV,
    RedisKV,
    get_kv,
    kv_initialized,
    publish_soon,
    reset_kv,
)


@pytest.fixture(autouse=True)
def _reset_singleton(_no_nats_sidecar):
    reset_kv()
    yield
    reset_kv()


# ---------------------------------------------------------------------------
# MemoryKV
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_kv_set_get_delete() -> None:
    kv = MemoryKV()
    await kv.set("k", "v")
    assert await kv.get("k") == "v"
    await kv.delete("k")
    assert await kv.get("k") is None


@pytest.mark.asyncio
async def test_memory_kv_ttl_expira(monkeypatch: pytest.MonkeyPatch) -> None:
    kv = MemoryKV()
    await kv.set("k", "v", ttl_s=0.01)
    await asyncio.sleep(0.05)
    assert await kv.get("k") is None


@pytest.mark.asyncio
async def test_memory_kv_pubsub_sync_e_async() -> None:
    kv = MemoryKV()
    received: list[str] = []

    def sync_cb(payload: str) -> None:
        received.append(f"sync:{payload}")

    async def async_cb(payload: str) -> None:
        received.append(f"async:{payload}")

    kv.subscribe("ch", sync_cb)
    kv.subscribe("ch", async_cb)
    await kv.start()
    await kv.publish("ch", "olá")
    assert received == ["sync:olá", "async:olá"]


@pytest.mark.asyncio
async def test_memory_kv_subscriber_com_erro_nao_propaga() -> None:
    kv = MemoryKV()
    received: list[str] = []

    def broken(_: str) -> None:
        raise RuntimeError("boom")

    kv.subscribe("ch", broken)
    kv.subscribe("ch", received.append)
    await kv.publish("ch", "x")
    # O segundo subscriber ainda recebe mesmo com o primeiro falhando.
    assert received == ["x"]


# ---------------------------------------------------------------------------
# RedisKV (via fakeredis)
# ---------------------------------------------------------------------------


def _fake_redis_kv() -> RedisKV:
    import fakeredis.aioredis

    instance = RedisKV.__new__(RedisKV)
    instance._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    instance._subs = {}
    instance._pubsub = None
    instance._reader = None
    return instance


@pytest.mark.asyncio
async def test_redis_kv_set_get_delete() -> None:
    kv = _fake_redis_kv()
    await kv.set("k", "v", ttl_s=60)
    assert await kv.get("k") == "v"
    await kv.delete("k")
    assert await kv.get("k") is None
    await kv.close()


@pytest.mark.asyncio
async def test_redis_kv_pubsub_dispatch() -> None:
    kv = _fake_redis_kv()
    received: list[str] = []
    kv.subscribe("ch", received.append)
    await kv.start()
    await kv.publish("ch", "msg")
    # Reader roda em task separada — espera curta com retry.
    for _ in range(50):
        if received:
            break
        await asyncio.sleep(0.01)
    assert received == ["msg"]
    await kv.close()


# ---------------------------------------------------------------------------
# Factory + publish_soon
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_kv_default_memoria(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.settings import settings

    monkeypatch.setattr(settings, "redis_url", None)
    assert isinstance(await get_kv(), MemoryKV)


@pytest.mark.asyncio
async def test_get_kv_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.settings import settings

    monkeypatch.setattr(settings, "redis_url", None)
    assert await get_kv() is await get_kv()


@pytest.mark.asyncio
async def test_get_kv_lite_ignora_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Em modo lite, redis_url é ignorado mesmo se setado — sem tocar a rede."""
    from backend.settings import settings

    monkeypatch.setattr(settings, "storage_mode", "lite")
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")

    def _boom(_url: str) -> bool:  # pragma: no cover - não deve ser chamado
        raise AssertionError("redis_reachable não deve ser consultado em lite")

    monkeypatch.setattr(kv_mod, "redis_reachable", _boom)
    assert isinstance(await get_kv(), MemoryKV)


@pytest.mark.asyncio
async def test_get_kv_complete_redis_inacessivel_tenta_nats_e_cai_em_memoria(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Em modo complete com Redis inacessível, tenta NATS e cai em MemoryKV sem o sidecar."""
    from unittest.mock import AsyncMock

    from backend.scheduling import nats_sidecar
    from backend.settings import settings

    monkeypatch.setattr(settings, "storage_mode", "complete")
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(kv_mod, "redis_reachable", lambda _url: False)
    monkeypatch.setattr(
        nats_sidecar, "ensure_nats_sidecar", AsyncMock(return_value=None)
    )
    assert isinstance(await get_kv(), MemoryKV)


@pytest.mark.asyncio
async def test_get_kv_sem_redis_usa_nats_quando_sidecar_disponivel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sem Redis mas com o sidecar NATS de pé, usa NatsKV (persistência p/ todos)."""
    from unittest.mock import AsyncMock

    from backend.scheduling import nats_sidecar
    from backend.settings import settings

    monkeypatch.setattr(settings, "storage_mode", "complete")
    monkeypatch.setattr(settings, "redis_url", None)
    monkeypatch.setattr(
        nats_sidecar,
        "ensure_nats_sidecar",
        AsyncMock(return_value="nats://127.0.0.1:4222"),
    )
    result = await get_kv()
    assert isinstance(result, kv_mod.NatsKV)


@pytest.mark.asyncio
async def test_publish_soon_entrega_no_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.settings import settings

    monkeypatch.setattr(settings, "redis_url", None)
    received: list[str] = []
    kv = await get_kv()
    kv.subscribe("ch", received.append)
    publish_soon("ch", "payload")
    await asyncio.sleep(0.05)  # deixa a task agendada rodar
    assert received == ["payload"]


def test_publish_soon_sem_loop_e_noop() -> None:
    # Fora de event loop não deve lançar (CLI puro / import time).
    publish_soon("ch", "x")


class TestKvInitialized:
    """`kv_initialized()` — sem efeito colateral, usada no shutdown pra
    nunca subir o KV (e o sidecar NATS por trás dele) só pra fechá-lo."""

    def test_false_antes_de_qualquer_get_kv(self) -> None:
        assert kv_initialized() is False

    @pytest.mark.asyncio
    async def test_true_depois_de_get_kv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from backend.settings import settings

        monkeypatch.setattr(settings, "redis_url", None)
        await get_kv()
        assert kv_initialized() is True

    def test_false_de_novo_depois_de_reset(self) -> None:
        reset_kv()
        assert kv_initialized() is False
