"""Ciclo de vida do sidecar Electron contra o processo real — skip se o
build de dev não existir (mesma resolução usada em produção pela decisão
`should_spawn_electron`, ver test_electron_launcher.py).

Diferente de test_electron_sidecar.py (100% mock de
asyncio.create_subprocess_exec) e de test_electron_launcher.py (testa o
lado Electron da conexão attached), este arquivo testa o PRÓPRIO ciclo de
vida do sidecar do lado backend: spawn real, reuso do mesmo processo,
shutdown idempotente e comportamento sob chamadas concorrentes — mesmo
roteiro que `test_nats_sidecar_real.py` já cobre pro NATS, incluindo o
mesmo edge case de concorrência que encontrou um bug real de produção lá
(lock preso a um event loop de outro teste).
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from backend.services import electron_sidecar
from backend.services.electron_launcher import resolve_electron_launch

pytestmark = pytest.mark.skipif(
    resolve_electron_launch() is None,
    reason="build de dev do Electron ausente — rode `scons frontend` (ou "
    "`pnpm --dir frontend install && pnpm --dir frontend run "
    "electron:build`)",
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
async def _clean_sidecar_state(monkeypatch: pytest.MonkeyPatch):
    """Garante nenhum sidecar sobrando de um teste anterior, antes e depois.

    Aponta VECTORA_PORT pra uma porta livre sem nada escutando — sem isso
    o Electron herdaria o ambiente do processo pytest (sem VECTORA_PORT) e
    ficaria retentando o health-check por até 90s (READINESS_TIMEOUT_MS)
    antes de desistir sozinho; aqui ele falha rápido, sem impacto no teste
    (o ciclo de vida do sidecar não depende do health-check ter sucesso).
    """
    monkeypatch.setenv("VECTORA_PORT", str(_free_port()))
    await electron_sidecar.stop_electron_sidecar()
    yield
    await electron_sidecar.stop_electron_sidecar()


@pytest.mark.asyncio
async def test_ensure_electron_sidecar_sobe_processo_real():
    proc = await electron_sidecar.ensure_electron_sidecar()

    assert proc is not None
    assert electron_sidecar._proc is proc
    assert proc.returncode is None, "processo deveria seguir vivo"

    # Par de erro no mesmo teste: fecha o ciclo spawn→vivo→morto.
    await electron_sidecar.stop_electron_sidecar()
    for _ in range(50):
        if proc.returncode is not None:
            break
        await asyncio.sleep(0.1)
    else:
        pytest.fail("processo Electron continuou vivo após stop_electron_sidecar()")


@pytest.mark.asyncio
async def test_ensure_electron_sidecar_reusa_processo_ja_rodando_de_verdade():
    first = await electron_sidecar.ensure_electron_sidecar()
    assert first is not None
    first_pid = first.pid

    second = await electron_sidecar.ensure_electron_sidecar()
    assert second is not None

    assert second.pid == first_pid


@pytest.mark.asyncio
async def test_stop_electron_sidecar_e_idempotente_com_processo_real():
    await electron_sidecar.ensure_electron_sidecar()

    await electron_sidecar.stop_electron_sidecar()
    await electron_sidecar.stop_electron_sidecar()  # segunda chamada não deve lançar

    assert electron_sidecar._proc is None


@pytest.mark.asyncio
async def test_ensure_electron_sidecar_chamado_concorrentemente_nao_duplica_processo():
    """Regra 18 — edge case de concorrência (mesma lição de
    test_nats_sidecar_real.py — se este teste falhar, é um bug de produção
    real, não do teste)."""
    proc_a, proc_b = await asyncio.gather(
        electron_sidecar.ensure_electron_sidecar(),
        electron_sidecar.ensure_electron_sidecar(),
    )

    assert proc_a is not None
    assert proc_a is proc_b
