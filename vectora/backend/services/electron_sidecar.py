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
import signal
import sys

from backend.services.electron_launcher import resolve_electron_launch
from backend.services.subprocess_logging import pipe_to_logger
from backend.services.subprocess_sidecar_utils import LazyLock, terminate_gracefully
from backend.services.tray import has_display

logger = logging.getLogger(__name__)

_proc: asyncio.subprocess.Process | None = None
_log_task: asyncio.Task | None = None
_watch_task: asyncio.Task | None = None
_spawn_lock = LazyLock()

# Handle da Windows Job Object (ver `_assign_to_job_object_best_effort`) —
# vive pelo tempo de vida do processo Python inteiro, nunca fechado
# explicitamente (mesmo padrão de `backend/scheduling/nats_sidecar.py`).
_job_handle: int | None = None

# Exit status used by Electron to request a backend-managed restart.
_ELECTRON_RESTART_EXIT_CODE = 42


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


async def ensure_electron_sidecar() -> asyncio.subprocess.Process | None:
    """Sobe o Electron (dev) como sidecar, se ainda não estiver rodando.

    Idempotente — chamadas concorrentes reusam o mesmo processo (mesmo
    padrão de ``_spawn_lock`` de ``nats_sidecar.py``, que evitou dois
    processos subindo em corrida). Lê porta/pipe já decididos por
    ``_run_start`` via ``VECTORA_PORT``/``VECTORA_IPC_PIPE`` no ambiente do
    próprio processo — nunca lança, retorna ``None`` em qualquer falha (o
    backend segue de pé sem janela).
    """
    global _proc, _log_task, _watch_task

    async with _spawn_lock.get():
        if _proc is not None and _proc.returncode is None:
            return _proc

        launch = resolve_electron_launch()
        if launch is None:
            return None
        exe, exe_args = launch

        env = {**os.environ, "VECTORA_EXTERNAL_BACKEND": "1"}
        try:
            proc = await asyncio.create_subprocess_exec(
                exe,
                *exe_args,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except Exception:
            logger.exception("electron_sidecar: falha ao spawnar Electron (dev)")
            return None

        logger.info("electron_sidecar: Electron (dev) spawnado, pid=%d", proc.pid)
        _proc = proc
        _assign_to_job_object_best_effort(proc.pid)
        _log_task = asyncio.create_task(
            pipe_to_logger(proc.stdout, logger, prefix="electron")
        )
        _watch_task = asyncio.create_task(_watch_for_unexpected_exit(proc))
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
    """Reage ao encerramento do Electron sem derrubar um backend reutilizável.

    Uma saída normal (código 0) acontece durante o reinício pelo tray. O
    backend deve permanecer vivo para que a nova instância do Electron possa
    reconectar ao mesmo named pipe/porta. Apenas uma saída anormal encerra o
    processo pai; isso mantém o diagnóstico de crashes sem transformar um
    reinício normal em ``ETIMEDOUT`` no frontend.

    Não dispara se a saída foi pedida por ``stop_electron_sidecar()`` (que já
    zera ``_proc`` antes de terminar o processo, então este proc deixa de ser
    o atual).
    """
    global _proc

    await proc.wait()
    if _proc is not proc:
        return
    if proc.returncode == _ELECTRON_RESTART_EXIT_CODE:
        logger.info("electron_sidecar: reinício solicitado pelo Electron")
        _proc = None
        await ensure_electron_sidecar()
        return
    logger.info(
        "electron_sidecar: Electron saiu anormalmente (code=%s) — "
        "encerrando o processo backend",
        proc.returncode,
    )
    os.kill(os.getpid(), signal.SIGTERM)


async def stop_electron_sidecar() -> None:
    """Encerra o sidecar Electron, se estiver rodando. Idempotente."""
    global _proc, _log_task, _watch_task

    _spawn_lock.reset()  # mesmo motivo do nats_sidecar: solta o lock preso ao loop

    if _log_task is not None:
        _log_task.cancel()
        _log_task = None
    if _watch_task is not None:
        _watch_task.cancel()
        _watch_task = None

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
        "electron_sidecar: processo pid=%d encerrado (code=%s)",
        proc.pid,
        proc.returncode,
    )
