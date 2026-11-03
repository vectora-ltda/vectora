"""Electron como sidecar do backend — spawnado de dentro do lifespan do
FastAPI (mesmo padrão async de ``backend/scheduling/nats_sidecar.py``), não
do bootstrap síncrono da CLI (``backend/main.py::_run_start``).

Isso mantém `vectora start` leve pra quem só quer a API REST (VPS/Docker/
CI, ou qualquer client batendo direto no backend): o processo ASGI decide
sozinho, no seu próprio startup, se faz sentido subir uma janela — o
Electron nasce (ou não) como qualquer outro sidecar opcional (NATS,
embedding worker, etc.), não como parte do bootstrap da CLI.

A decisão de *tentar* (``should_spawn_electron``) ainda precisa acontecer
cedo, em ``_run_start``, porque ela também decide o transporte IPC (unix
socket/named pipe) antes do uvicorn subir — mas o *spawn* em si só roda
aqui, dentro do event loop já rodando do FastAPI.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import signal
import socket
import sys
from collections.abc import Callable

from backend.services.electron_launcher import resolve_electron_launch
from backend.services.subprocess_logging import pipe_to_logger
from backend.services.subprocess_sidecar_utils import (
    LazyLock,
    sidecar_process_options,
    terminate_gracefully,
)
from backend.services.tray import has_display

logger = logging.getLogger(__name__)

_proc: asyncio.subprocess.Process | None = None
_log_task: asyncio.Task | None = None
_watch_task: asyncio.Task | None = None
_window_task: asyncio.Task | None = None
_startup_failed = False
_spawn_lock = LazyLock()
_backend_shutdown_callback: Callable[[], None] | None = None

# Handle da Windows Job Object (ver `_assign_to_job_object_best_effort`) —
# vive pelo tempo de vida do processo Python inteiro, nunca fechado
# explicitamente (mesmo padrão de `backend/scheduling/nats_sidecar.py`).
_job_handle: int | None = None

# Exit status used by Electron to request a backend-managed restart. A normal
# exit remains an intentional shutdown signal (for example, tray "Sair").
ELECTRON_RESTART_EXIT_CODE = 42

#: Linha que o Electron imprime no stdout quando a janela principal aparece
#: (``frontend/electron/src/main.ts``). Sem ela dentro do prazo, o desktop
#: não abriu e `vectora start` encerra em vez de seguir como servidor web.
WINDOW_READY_MARKER = "VECTORA_ELECTRON_WINDOW_READY"
_DEFAULT_WINDOW_TIMEOUT_S = 120.0


def _ensure_electron_cdp_port() -> str:
    """Reserve a loopback port value shared by backend and Electron."""
    configured = os.environ.get("VECTORA_ELECTRON_CDP_PORT", "").strip()
    if configured.isdigit() and 1 <= int(configured) <= 65535:
        return configured
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = str(probe.getsockname()[1])
    os.environ["VECTORA_ELECTRON_CDP_PORT"] = port
    return port


def _ensure_electron_cdp_proxy_port() -> str:
    """Choose the authenticated proxy port without reusing a fixed default."""
    configured = os.environ.get("VECTORA_ELECTRON_CDP_PROXY_PORT", "").strip()
    if configured.isdigit() and 1 <= int(configured) <= 65535:
        return configured
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = str(probe.getsockname()[1])
    os.environ["VECTORA_ELECTRON_CDP_PROXY_PORT"] = port
    return port


def _ensure_electron_cdp_auth_token() -> str:
    """Create a per-process bearer token for the Electron CDP proxy."""
    token = os.environ.get("VECTORA_ELECTRON_CDP_AUTH_TOKEN", "").strip()
    if not token:
        token = secrets.token_urlsafe(32)
        os.environ["VECTORA_ELECTRON_CDP_AUTH_TOKEN"] = token
    return token


def set_backend_shutdown_callback(callback: Callable[[], None] | None) -> None:
    """Registra o callback do processo dono para desligamento coordenado.

    No Windows, ``os.kill(..., SIGTERM)`` pode encerrar o processo
    imediatamente sem executar o handler Python. O callback mantém o
    shutdown dentro do event loop, permitindo que o FastAPI feche NATS e os
    demais sidecars antes de terminar.
    """
    global _backend_shutdown_callback
    _backend_shutdown_callback = callback


def should_spawn_electron() -> bool:
    """True quando este processo deve se autoeleger e subir o Electron:
    não já rodando sob Electron (``VECTORA_DESKTOP`` setado externamente),
    sem ``--headless``, com display disponível, e o build de dev do
    Electron resolvível (``scons frontend``/``pnpm --dir frontend run
    electron:build`` já rodou). Chamada por ``_run_start`` (decide transporte IPC cedo) e
    reaproveitada aqui só como referência — a decisão real de spawnar
    chega via env (``VECTORA_SPAWN_ELECTRON``), setado por quem chamou
    esta função primeiro.
    """
    if os.environ.get("VECTORA_DESKTOP"):
        return False
    if os.environ.get("VECTORA_HEADLESS"):
        return False
    if not has_display():
        return False
    return resolve_electron_launch() is not None


def desktop_build_missing() -> bool:
    """True quando `vectora start` deveria abrir o desktop e não consegue.

    Há display, o processo não é headless nem foi lançado pelo Electron, e
    nenhum build do Electron resolve. Sem display (VPS/Docker) ou com
    ``--headless`` o servidor puro continua sendo o modo correto.
    """
    if os.environ.get("VECTORA_DESKTOP") or os.environ.get("VECTORA_HEADLESS"):
        return False
    if not has_display():
        return False
    return resolve_electron_launch() is None


def startup_failed() -> bool:
    """True quando o backend encerrou porque a janela do Electron não abriu."""
    return _startup_failed


def _window_timeout_s() -> float:
    """Prazo para a janela abrir; ``VECTORA_ELECTRON_WINDOW_TIMEOUT_S`` > 0."""
    raw = os.environ.get("VECTORA_ELECTRON_WINDOW_TIMEOUT_S", "").strip()
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_WINDOW_TIMEOUT_S
    return value if value > 0 else _DEFAULT_WINDOW_TIMEOUT_S


async def _watch_window_ready(
    proc: asyncio.subprocess.Process, ready: asyncio.Event, timeout_s: float
) -> None:
    """Encerra o backend se o Electron não mostrar a janela dentro do prazo."""
    global _startup_failed

    try:
        await asyncio.wait_for(ready.wait(), timeout_s)
        return
    except TimeoutError:
        pass
    if _proc is not proc or proc.returncode is not None:
        return
    _startup_failed = True
    logger.error(
        "electron_sidecar: o Electron (pid=%d) não abriu a janela em %.0fs — "
        "encerrando o Vectora. Verifique se o binário do Electron executa "
        "(disco lento, antivírus bloqueando) ou use `vectora web` para abrir "
        "pelo navegador.",
        proc.pid,
        timeout_s,
    )
    if _backend_shutdown_callback is not None:
        _backend_shutdown_callback()
    else:
        os.kill(os.getpid(), signal.SIGTERM)


async def ensure_electron_sidecar() -> asyncio.subprocess.Process | None:
    """Sobe o Electron (dev) como sidecar, se ainda não estiver rodando.

    Idempotente — chamadas concorrentes reusam o mesmo processo (mesmo
    padrão de ``_spawn_lock`` de ``nats_sidecar.py``, que evitou dois
    processos subindo em corrida). Lê porta/pipe já decididos por
    ``_run_start`` via ``VECTORA_PORT``/``VECTORA_IPC_PIPE`` no ambiente do
    próprio processo — nunca lança, retorna ``None`` em qualquer falha (o
    backend segue de pé sem janela).
    """
    global _proc, _log_task, _watch_task, _window_task

    async with _spawn_lock.get():
        if _proc is not None and _proc.returncode is None:
            return _proc

        launch = resolve_electron_launch()
        if launch is None:
            return None
        exe, exe_args = launch

        cdp_port = _ensure_electron_cdp_port()
        cdp_proxy_port = _ensure_electron_cdp_proxy_port()
        cdp_auth_token = _ensure_electron_cdp_auth_token()
        env = {
            **os.environ,
            "VECTORA_EXTERNAL_BACKEND": "1",
            "VECTORA_ELECTRON_CDP_PORT": cdp_port,
            "VECTORA_ELECTRON_CDP_PROXY_PORT": cdp_proxy_port,
            "VECTORA_ELECTRON_CDP_AUTH_TOKEN": cdp_auth_token,
        }
        try:
            proc = await asyncio.create_subprocess_exec(
                exe,
                *exe_args,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                **sidecar_process_options(),
            )
        except Exception:
            logger.exception("electron_sidecar: falha ao spawnar Electron (dev)")
            return None

        logger.info("electron_sidecar: Electron (dev) spawnado, pid=%d", proc.pid)
        _proc = proc
        _assign_to_job_object_best_effort(proc.pid)
        window_ready = asyncio.Event()

        def _on_line(line: str) -> None:
            if WINDOW_READY_MARKER in line:
                window_ready.set()

        _log_task = asyncio.create_task(
            pipe_to_logger(proc.stdout, logger, prefix="electron", on_line=_on_line)
        )
        _watch_task = asyncio.create_task(_watch_for_unexpected_exit(proc))
        if _window_task is not None:
            _window_task.cancel()
            _window_task = None
        # `vectora web` mantém o Electron oculto de propósito: não há janela a
        # esperar nesse modo.
        if not os.environ.get("VECTORA_WEB_MODE"):
            _window_task = asyncio.create_task(
                _watch_window_ready(proc, window_ready, _window_timeout_s())
            )
        return proc


def _assign_to_job_object_best_effort(pid: int) -> None:
    """Defesa em profundidade (Windows only) contra o processo Electron
    sobreviver a um `SIGKILL`/fechamento abrupto do terminal que rodou
    `vectora start` — sem isso, fechar o terminal deixa o Electron (e o
    ícone do tray) órfão, ainda segurando arquivos abertos em
    `~/.vectora`. Mesmo mecanismo (`KILL_ON_JOB_CLOSE`) já usado pelo
    `nats-server` em `backend/scheduling/nats_sidecar.py`. Best-effort:
    qualquer falha aqui é só logada, nunca impede o sidecar de subir.
    """
    global _job_handle
    if sys.platform != "win32":
        return
    try:
        from backend.services.win_job_object import (
            assign_process_to_job,
            create_job_object,
        )

        if _job_handle is None:
            _job_handle = create_job_object()
        if _job_handle is not None:
            assign_process_to_job(_job_handle, pid)
    except Exception:
        logger.warning(
            "electron_sidecar: falha ao associar Electron à Job Object do Windows",
            exc_info=True,
        )


async def _watch_for_unexpected_exit(proc: asyncio.subprocess.Process) -> None:
    """Reage ao encerramento do Electron e propaga a saída para o processo dono.

    O Electron usa ``ELECTRON_RESTART_EXIT_CODE`` durante o reinício pelo
    tray. O backend permanece vivo e registra a nova instância para que ela
    possa reconectar ao mesmo named pipe/porta. Uma saída normal (código 0),
    como ``Ctrl+C`` propagado pelo terminal ou o comando tray ``Sair``,
    solicita o shutdown gracioso do processo dono para que todos os sidecars
    sejam fechados juntos.

    Não dispara se a saída foi pedida por ``stop_electron_sidecar()`` (que já
    zera ``_proc`` antes de terminar o processo, então este proc deixa de ser
    o atual).
    """
    global _proc

    await proc.wait()
    if _proc is not proc:
        return
    if proc.returncode == ELECTRON_RESTART_EXIT_CODE:
        logger.info("electron_sidecar: reinício solicitado pelo Electron")
        _proc = None
        await ensure_electron_sidecar()
        return
    logger.info(
        "electron_sidecar: Electron encerrou (code=%s) — solicitando shutdown do backend",
        proc.returncode,
    )
    if _backend_shutdown_callback is not None:
        _backend_shutdown_callback()
    else:
        # Fallback para usos do sidecar fora do launcher principal.
        os.kill(os.getpid(), signal.SIGTERM)


async def stop_electron_sidecar() -> None:
    """Encerra o sidecar Electron, se estiver rodando. Idempotente."""
    global _proc, _log_task, _watch_task, _window_task

    _spawn_lock.reset()  # mesmo motivo do nats_sidecar: solta o lock preso ao loop

    if _log_task is not None:
        _log_task.cancel()
        _log_task = None
    if _watch_task is not None:
        _watch_task.cancel()
        _watch_task = None
    if _window_task is not None and _window_task is not asyncio.current_task():
        _window_task.cancel()
    _window_task = None

    if _proc is None:
        # S0-7: se uma janela do Electron ainda estiver na tela quando este
        # log aparecer, a causa não é o mecanismo de kill (confirmado
        # funcional — `terminate_gracefully` mata processo + renderers sem
        # órfão) — é `_proc` já estar `None`/desincronizado da janela real
        # nesse ponto (ex.: `_watch_for_unexpected_exit` limpou a
        # referência por engano). Nível INFO: idempotente e comum (chamado
        # de novo num shutdown que já rodou), não é por si só um problema.
        logger.info(
            "electron_sidecar: stop chamado sem processo rastreado "
            "(já encerrado, ou nunca chegou a subir)"
        )
        return
    proc = _proc
    _proc = None
    if proc.returncode is not None:
        logger.info(
            "electron_sidecar: processo pid=%d já havia saído (code=%s)",
            proc.pid,
            proc.returncode,
        )
        return
    await terminate_gracefully(
        proc, timeout_seconds=10.0, logger=logger, log_prefix="electron_sidecar"
    )
    logger.info(
        "electron_sidecar: processo pid=%d encerrado durante shutdown (code=%s)",
        proc.pid,
        proc.returncode,
    )
