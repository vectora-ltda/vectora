"""NatsMQ/NatsKV contra o sidecar NATS de verdade — skip se o binário não existe.

Diferente de test_nats_mq_kv.py (mock total do client, sempre roda), este
arquivo sobe o `nats-server` real via `ensure_nats_sidecar()` e valida o
CONTRATO da interface (tipo do id retornado, atributos do StreamMessage,
round-trip get/set) — não conteúdo fixo. Mesmo padrão de
`test_storage_qdrant.py` (`@pytest.mark.storage`, skip limpo sem o serviço
disponível).

O skip-guard usa a MESMA resolução (`_resolve_binary()`) que o sidecar usa em
produção — PATH (``choco install nats-server`` / ``brew install
nats-server``) ou o binário baixado em ``resources/`` por
``scons nats``. Um guard que só olhasse PATH (como uma versão anterior deste
arquivo fazia) pula sempre mesmo com o binário bundlado presente.
"""

from __future__ import annotations

import asyncio

import pytest

from backend.persistence.kv import NatsKV
from backend.scheduling.mq import NatsMQ, StreamMessage
from backend.scheduling.nats_sidecar import (
    _resolve_binary,
    ensure_nats_sidecar,
    stop_nats_sidecar,
)

pytestmark = pytest.mark.skipif(
    _resolve_binary() is None,
    reason="nats-server não encontrado (PATH nem resources/) — rode `scons nats`",
)


@pytest.fixture
async def sidecar_url():
    url = await ensure_nats_sidecar()
    assert url is not None, "nats-server presente no PATH mas o sidecar não subiu"
    yield url
    await stop_nats_sidecar()


@pytest.mark.asyncio
async def test_nats_mq_enqueue_returns_nonempty_string_id(sidecar_url):
    mq = NatsMQ(sidecar_url)
    try:
        msg_id = await mq.enqueue("test-stream-real", {"a": 1})
        assert isinstance(msg_id, str)
        assert msg_id != ""
    finally:
        await mq.close()


@pytest.mark.asyncio
async def test_nats_mq_consume_delivers_stream_message_shape(sidecar_url):
    mq = NatsMQ(sidecar_url)
    received: list[StreamMessage] = []
    stop = asyncio.Event()

    async def handler(msg: StreamMessage) -> None:
        received.append(msg)
        stop.set()

    try:
        await mq.enqueue("test-stream-shape", {"any": "payload"})
        await asyncio.wait_for(
            mq.consume(
                "test-stream-shape", "g1", "c1", handler, stop_event=stop, block_ms=500
            ),
            timeout=10,
        )
    finally:
        await mq.close()

    assert len(received) == 1
    msg = received[0]
    assert isinstance(msg.id, str)
    assert isinstance(msg.payload, dict)


@pytest.mark.asyncio
async def test_nats_kv_get_set_delete_round_trip_real(sidecar_url):
    kv = NatsKV(sidecar_url)
    try:
        await kv.set("real-key", "real-value")
        value = await kv.get("real-key")
        assert value == "real-value"

        await kv.delete("real-key")
        after_delete = await kv.get("real-key")
        assert after_delete is None
    finally:
        await kv.close()


@pytest.mark.asyncio
async def test_nats_kv_get_missing_key_returns_none_type(sidecar_url):
    kv = NatsKV(sidecar_url)
    try:
        value = await kv.get("chave-que-nunca-existiu")
        assert value is None
    finally:
        await kv.close()
