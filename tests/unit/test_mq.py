"""Testes da message queue (src/services/mq.py)."""

from __future__ import annotations

import asyncio

import pytest

from backend.scheduling.mq import (
    MemoryMQ,
    RedisMQ,
    StreamMessage,
    get_mq,
    mq_initialized,
    reset_mq,
)


@pytest.fixture(autouse=True)
def _reset_singleton(_no_nats_sidecar):
    reset_mq()
    yield
    reset_mq()


# ---------------------------------------------------------------------------
# MemoryMQ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_mq_enqueue_consume() -> None:
    mq = MemoryMQ()
    received: list[StreamMessage] = []
    stop = asyncio.Event()

    async def handler(msg: StreamMessage) -> None:
        received.append(msg)
        stop.set()

    await mq.enqueue("jobs", {"a": 1})
    await asyncio.wait_for(
        mq.consume("jobs", "g", "c1", handler, stop_event=stop, block_ms=100),
        timeout=5,
    )
    assert len(received) == 1
    assert received[0].payload == {"a": 1}


@pytest.mark.asyncio
async def test_memory_mq_handler_falha_reenfileira() -> None:
    mq = MemoryMQ()
    attempts: list[int] = []
    stop = asyncio.Event()

    async def flaky(msg: StreamMessage) -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("primeira tentativa falha")
        stop.set()

    await mq.enqueue("jobs", {"x": 1})
    await asyncio.wait_for(
        mq.consume("jobs", "g", "c1", flaky, stop_event=stop, block_ms=100),
        timeout=10,
    )
    assert len(attempts) == 2  # redelivery após falha


# ---------------------------------------------------------------------------
# RedisMQ (via fakeredis)
# ---------------------------------------------------------------------------


def _fake_redis_mq() -> RedisMQ:
    import fakeredis.aioredis

    instance = RedisMQ.__new__(RedisMQ)
    instance._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    instance._maxlen = 1000
    return instance


@pytest.mark.asyncio
async def test_redis_mq_enqueue_consume_ack() -> None:
    mq = _fake_redis_mq()
    received: list[StreamMessage] = []
    stop = asyncio.Event()

    async def handler(msg: StreamMessage) -> None:
        received.append(msg)
        stop.set()

    msg_id = await mq.enqueue("jobs", {"hello": "world"})
    assert msg_id
    await asyncio.wait_for(
        mq.consume("jobs", "g1", "c1", handler, stop_event=stop, block_ms=100),
        timeout=5,
    )
    assert received[0].payload == {"hello": "world"}
    # ACK feito: nenhuma pendência no grupo.
    pending = await mq._redis.xpending("jobs", "g1")
    assert pending["pending"] == 0
    await mq.close()


@pytest.mark.asyncio
async def test_redis_mq_handler_falha_fica_pendente() -> None:
    mq = _fake_redis_mq()
    stop = asyncio.Event()

    async def broken(msg: StreamMessage) -> None:
        stop.set()
        raise RuntimeError("boom")

    await mq.enqueue("jobs", {"x": 1})
    await asyncio.wait_for(
        mq.consume("jobs", "g1", "c1", broken, stop_event=stop, block_ms=100),
        timeout=5,
    )
    # Sem ACK: mensagem permanece pendente para redelivery.
    pending = await mq._redis.xpending("jobs", "g1")
    assert pending["pending"] == 1
    await mq.close()


@pytest.mark.asyncio
async def test_redis_mq_grupo_idempotente() -> None:
    mq = _fake_redis_mq()
    await mq._ensure_group("jobs", "g1")
    await mq._ensure_group("jobs", "g1")  # BUSYGROUP suprimido
    await mq.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_mq_default_memoria(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.settings import settings

    monkeypatch.setattr(settings, "redis_url", None)
    mq = await get_mq()
    assert isinstance(mq, MemoryMQ)
    assert mq is await get_mq()


@pytest.mark.asyncio
async def test_get_mq_lite_ignora_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """Em modo lite, redis_url é ignorado — sem consultar a rede."""
    from backend.persistence import kv as kv_mod
    from backend.settings import settings

    monkeypatch.setattr(settings, "storage_mode", "lite")
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")

    def _boom(_url: str) -> bool:  # pragma: no cover - não deve ser chamado
        raise AssertionError("redis_reachable não deve ser consultado em lite")

    monkeypatch.setattr(kv_mod, "redis_reachable", _boom)
    assert isinstance(await get_mq(), MemoryMQ)


@pytest.mark.asyncio
async def test_get_mq_complete_redis_inacessivel_tenta_nats_e_cai_em_memoria(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Em modo complete com Redis inacessível, tenta NATS e cai em MemoryMQ sem o sidecar."""
    from unittest.mock import AsyncMock

    from backend.persistence import kv as kv_mod
    from backend.scheduling import nats_sidecar
    from backend.settings import settings

    monkeypatch.setattr(settings, "storage_mode", "complete")
    monkeypatch.setattr(settings, "redis_url", "redis://localhost:6379/0")
    monkeypatch.setattr(kv_mod, "redis_reachable", lambda _url: False)
    monkeypatch.setattr(
        nats_sidecar, "ensure_nats_sidecar", AsyncMock(return_value=None)
    )
    assert isinstance(await get_mq(), MemoryMQ)


@pytest.mark.asyncio
async def test_get_mq_sem_redis_usa_nats_quando_sidecar_disponivel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sem Redis mas com o sidecar NATS de pé, usa NatsMQ (persistência p/ todos)."""
    from unittest.mock import AsyncMock

    from backend.scheduling import mq as mq_mod
    from backend.scheduling import nats_sidecar
    from backend.settings import settings

    monkeypatch.setattr(settings, "storage_mode", "complete")
    monkeypatch.setattr(settings, "redis_url", None)
    monkeypatch.setattr(
        nats_sidecar,
        "ensure_nats_sidecar",
        AsyncMock(return_value="nats://127.0.0.1:4222"),
    )
    result = await get_mq()
    assert isinstance(result, mq_mod.NatsMQ)


class TestMqInitialized:
    """`mq_initialized()` — sem efeito colateral, usada no shutdown pra
    nunca subir a fila (e o sidecar NATS por trás dela) só pra fechá-la."""

    def test_false_antes_de_qualquer_get_mq(self) -> None:
        assert mq_initialized() is False

    @pytest.mark.asyncio
    async def test_true_depois_de_get_mq(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from backend.settings import settings

        monkeypatch.setattr(settings, "redis_url", None)
        await get_mq()
        assert mq_initialized() is True

    def test_false_de_novo_depois_de_reset(self) -> None:
        reset_mq()
        assert mq_initialized() is False
