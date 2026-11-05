"""Ciclo de vida completo do sidecar de frontend: build real servido por um
backend spawnado como processo real via subprocess, health-check por HTTP
(sem ``VECTORA_DESKTOP=1`` — esse modo troca o transporte pra Unix Domain
Socket/named pipe em vez de TCP, incompatível com testar via HTTP simples).

Diferente do resto da suíte (``TestClient``/``AsyncClient`` in-process contra
a mesma app instanciada no processo de teste), aqui é um
``python -m backend.main start`` de verdade, pego pra capturar bugs que só
aparecem no boundary processo-a-processo: parsing de argumentos, resolução
de porta, bind de host, serving de estático fora do event loop do teste.
Skip limpo sem ``frontend/dist/index.html`` (roda ``pnpm --dir frontend
build`` antes).
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
from pathlib import Path

import httpx
import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_FRONTEND_DIST = _REPO_ROOT / "frontend" / "dist"


def _frontend_dist_available() -> bool:
    return (_FRONTEND_DIST / "index.html").is_file()


pytestmark = [
    pytest.mark.frontend_build,
    pytest.mark.skipif(
        not _frontend_dist_available(),
        reason="frontend/dist ausente — rode `pnpm --dir frontend build` antes",
    ),
]


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_background_tasks: set[asyncio.Task[None]] = set()


def _track(task: asyncio.Task[None]) -> None:
    """Guarda referência forte à task — sem isso o event loop pode coletar
    a task antes dela terminar (footgun documentado do asyncio.create_task)."""
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _wait_port_open(port: int, timeout_s: float) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    last_error: OSError | None = None
    while loop.time() < deadline:
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError as exc:
            last_error = exc
            await asyncio.sleep(0.2)
            continue
        writer.close()
        await writer.wait_closed()
        return
    raise TimeoutError(
        f"porta {port} não abriu em {timeout_s}s (último erro: {last_error})"
    )


async def _wait_port_closed(port: int, timeout_s: float) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        try:
            _, writer = await asyncio.open_connection("127.0.0.1", port)
        except OSError:
            return
        try:
            writer.close()
            await writer.wait_closed()
        except (ConnectionResetError, ConnectionAbortedError):
            # o processo servidor pode morrer entre o accept() do SO e o
            # nosso close() — o SO reseta a conexão em vez de fechar limpo;
            # isso não é evidência de porta ainda aberta, só um teardown em
            # andamento, então tenta de novo em vez de propagar.
            pass
        await asyncio.sleep(0.2)
    raise TimeoutError(f"porta {port} não fechou em {timeout_s}s")


async def _drain(stream: asyncio.StreamReader | None) -> None:
    """Consome stdout/stderr do processo pra não travar o pipe do SO."""
    if stream is None:
        return
    while not stream.at_eof():
        await stream.read(4096)


async def _spawn_backend(vectora_home: Path, port: int) -> asyncio.subprocess.Process:
    env = dict(os.environ)
    env["VECTORA_HOME"] = str(vectora_home)
    # Sem VECTORA_DESKTOP=1: em Linux/macOS esse modo troca o transporte
    # inteiro para Unix Domain Socket (nenhuma porta TCP aberta, por design
    # — desktop fala por IPC, nunca TCP), incompatível com o health-check
    # via HTTP que este teste faz. No Windows o modo desktop mantém a porta
    # TCP em paralelo ao named pipe, o que mascarava a lacuna localmente.
    env["VECTORA_UVICORN_LOG_LEVEL"] = "warning"
    env.pop("VECTORA_SKIP_STATIC", None)  # garante que frontend/dist é servido

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "backend.main",
        "start",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        cwd=str(_REPO_ROOT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _track(asyncio.create_task(_drain(proc.stdout)))
    _track(asyncio.create_task(_drain(proc.stderr)))
    return proc


@pytest.fixture
async def _spawned_backend(tmp_path: Path):
    port = _free_port()
    proc = await _spawn_backend(tmp_path, port)
    try:
        await _wait_port_open(port, timeout_s=60.0)
        yield proc, port
    finally:
        if proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=10.0)
            except TimeoutError:
                proc.kill()
                await proc.wait()


async def test_backend_spawnado_serve_a_spa_real_e_responde_health(_spawned_backend):
    _proc, port = _spawned_backend

    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}", timeout=10.0
    ) as client:
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        index = await client.get("/")
        assert index.status_code == 200
        assert "<html" in index.text.lower()

        # Par de erro: rota inexistente sob o catch-all da SPA cai no
        # index.html (roteamento client-side no browser), não em 404 puro —
        # comportamento documentado em server.py::_spa_or_static.
        missing = await client.get("/uma-rota-que-nao-existe-no-backend")
        assert missing.status_code == 200
        assert missing.text == index.text


async def test_encerrar_o_processo_libera_a_porta_de_verdade(_spawned_backend):
    proc, port = _spawned_backend

    proc.terminate()
    await asyncio.wait_for(proc.wait(), timeout=15.0)

    assert proc.returncode is not None
    await _wait_port_closed(port, timeout_s=10.0)
