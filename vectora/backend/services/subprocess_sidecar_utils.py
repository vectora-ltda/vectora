"""Padrões compartilhados entre sidecars de subprocesso (NATS, Electron
dev, ver ``backend/scheduling/nats_sidecar.py`` e ``backend/services/
electron_sidecar.py``) — lock de spawn lazy-init e encerramento gracioso
com timeout. Extraído porque os dois módulos tinham a mesma lógica
palavra-por-palavra.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import sys


async def _terminate_windows_tree(
    pid: int, logger: logging.Logger, log_prefix: str, timeout_seconds: float
) -> None:
    """Encerra descendentes de um sidecar no Windows.

    ``Process.terminate()`` atua apenas no PID raiz. Electron cria renderers
    e utilitários filhos, então ``taskkill /T /F`` garante que a árvore não
    fique órfã durante o encerramento do backend. No Windows, ``taskkill /F``
    é um fallback forçado: não representa um encerramento gracioso.
    """
    if sys.platform != "win32":
        return
    taskkill = shutil.which("taskkill")
    if taskkill is None:
        return
    try:
        killer = await asyncio.create_subprocess_exec(
            taskkill,
            "/PID",
            str(pid),
            "/T",
            "/F",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(killer.wait(), timeout=timeout_seconds)
        except TimeoutError:
            killer.kill()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(killer.wait(), timeout=timeout_seconds)
    except Exception:
        logger.debug("%s: taskkill da árvore falhou", log_prefix, exc_info=True)


class LazyLock:
    """``asyncio.Lock()`` criado sob demanda, não no import — um lock de
    módulo criado antes de qualquer event loop rodar fica preso ao
    primeiro loop que o tocar; uma segunda chamada com event loop novo
    (comum na suíte pytest-asyncio, um loop por teste) levanta "Lock is
    bound to a different event loop". Lazy-init garante que o lock sempre
    pertence ao loop atual. ``reset()`` solta a referência — usado no
    shutdown do sidecar, entre chamadas de teste."""

    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None

    def get(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def reset(self) -> None:
        self._lock = None


async def terminate_gracefully(
    proc: asyncio.subprocess.Process,
    *,
    timeout_seconds: float,
    logger: logging.Logger,
    log_prefix: str,
) -> None:
    """Solicita encerramento, aguarda com limite e aplica fallback forçado.

    ``terminate()`` → espera ``timeout_seconds``s → ``kill()`` se não morreu a
    tempo. No Windows, tanto ``terminate()`` quanto ``kill()`` chamam
    ``TerminateProcess``; ``taskkill /T /F`` também é forçado e existe apenas
    para limpar descendentes do sidecar. Nunca lança — best-effort, loga
    qualquer exceção além do timeout esperado.

    ``ProcessLookupError`` é tratado como caso esperado (idempotente), não
    erro: o processo já pode ter saído sozinho entre o momento em que o
    shutdown decide encerrá-lo e a chamada a ``terminate()`` — comum sob
    ``CancelledError`` do lifespan encadeando com o encerramento do
    sidecar. Logar isso como warning com traceback completo só polui o log
    de shutdown sem indicar nenhum problema real.
    """
    # No Windows, encerra a árvore enquanto o PID raiz ainda existe; depois
    # aguardamos o mesmo objeto asyncio para manter o contrato comum.
    await _terminate_windows_tree(proc.pid, logger, log_prefix, timeout_seconds)
    try:
        proc.terminate()
        await asyncio.wait_for(proc.wait(), timeout=timeout_seconds)
    except TimeoutError:
        proc.kill()
    except ProcessLookupError:
        logger.debug("%s: processo já havia saído antes do terminate()", log_prefix)
    except Exception:
        logger.warning("%s: erro ao encerrar", log_prefix, exc_info=True)
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=timeout_seconds)
