"""Ciclo de vida do sidecar `nats-server` contra o processo real — skip se o
binário não existe (mesma resolução de produção, ver test_nats_mq_kv_real.py).

Diferente de test_nats_sidecar.py (100% mock de asyncio.create_subprocess_exec)
e de test_nats_mq_kv_real.py (sobe o sidecar só como pré-condição pra testar
NatsMQ/NatsKV por cima), este arquivo testa o PRÓPRIO ciclo de vida do
sidecar: spawn real, porta de fato escutando, reuso do mesmo processo,
shutdown idempotente e comportamento sob chamadas concorrentes.
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from backend.scheduling import nats_sidecar
from backend.scheduling.nats_sidecar import (
    _resolve_binary,
    ensure_nats_sidecar,
    stop_nats_sidecar,
)

pytestmark = pytest.mark.skipif(
    _resolve_binary() is None,
    reason="nats-server não encontrado (PATH nem resources/) — rode `scons nats`",
)


@pytest.fixture(autouse=True)
async def _clean_sidecar_state():
    """Garante nenhum sidecar sobrando de um teste anterior, antes e depois."""
    await stop_nats_sidecar()
    yield
    await stop_nats_sidecar()


def _port_from_url(url: str) -> int:
    return int(url.rsplit(":", 1)[-1])


def _port_is_open(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


@pytest.mark.asyncio
async def test_ensure_nats_sidecar_sobe_processo_real_e_url_responde():
    url = await ensure_nats_sidecar()

    assert url is not None
    assert url.startswith("nats://127.0.0.1:")
    port = _port_from_url(url)
    assert _port_is_open(port), "porta do nats-server deveria estar escutando"
    assert nats_sidecar._proc is not None
    assert nats_sidecar._proc.returncode is None, "processo deveria seguir vivo"

    # Par de erro no mesmo teste: fecha o ciclo spawn→vivo→morto.
    await stop_nats_sidecar()
    for _ in range(20):
        if not _port_is_open(port, timeout=0.2):
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("porta do nats-server continuou aberta após stop_nats_sidecar()")


@pytest.mark.asyncio
async def test_ensure_nats_sidecar_reusa_processo_ja_rodando_de_verdade():
    first_url = await ensure_nats_sidecar()
    assert nats_sidecar._proc is not None
    first_pid = nats_sidecar._proc.pid

    second_url = await ensure_nats_sidecar()
    assert nats_sidecar._proc is not None
    second_pid = nats_sidecar._proc.pid

    assert second_url == first_url
    assert second_pid == first_pid


@pytest.mark.asyncio
async def test_stop_nats_sidecar_e_idempotente_com_processo_real():
    await ensure_nats_sidecar()

    await stop_nats_sidecar()
    await stop_nats_sidecar()  # segunda chamada não deve lançar

    assert nats_sidecar._proc is None
    assert nats_sidecar._url is None


@pytest.mark.asyncio
async def test_ensure_nats_sidecar_chamado_concorrentemente_nao_duplica_processo():
    """Regra 18 — edge case de concorrência.

    `ensure_nats_sidecar()` checa `_proc is not None` antes de spawnar, sem
    lock — sob corrida real, duas chamadas concorrentes podem passar pelo
    check antes de qualquer uma setar `_proc`. Se este teste falhar, é sinal
    de um bug de produção real (falta um asyncio.Lock em ensure_nats_sidecar),
    não um problema do teste.
    """
    url_a, url_b = await asyncio.gather(ensure_nats_sidecar(), ensure_nats_sidecar())

    assert url_a is not None
    assert url_a == url_b
    port = _port_from_url(url_a)
    assert _port_is_open(port)
