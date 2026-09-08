"""PtySession — pseudo-terminal cross-platform.

Abstrai ``pywinpty`` (Windows/ConPTY) e ``ptyprocess`` (macOS/Linux) atrás de
uma única classe assíncrona. I/O externo em **bytes** (o que o WebSocket envia
e recebe); a conversão para str (Windows) é feita dentro da classe.

A leitura roda num executor (a API dos backends é síncrona/bloqueante) e
empurra para uma ``asyncio.Queue`` consumida pelo handler WS.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import platform
import threading
from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from backend.sandbox.policy import SandboxPolicy

logger = logging.getLogger(__name__)

_IS_WINDOWS = platform.system() == "Windows"


def _load_pty_backend() -> Any:
    """Carrega o backend de PTY da plataforma: pywinpty no Windows, ptyprocess fora.

    Import dinâmico via importlib: as libs são mutuamente exclusivas por SO e não
    resolvem estaticamente na outra plataforma — resolver dinamicamente evita o
    type checker falhar em ``winpty`` no Linux (e vice-versa). Retorna None se o
    backend não estiver instalado; o caller degrada com erro tratado.
    """
    import importlib

    module = "winpty" if _IS_WINDOWS else "ptyprocess"
    try:
        return importlib.import_module(module).PtyProcess
    except Exception:  # pragma: no cover — backend de PTY indisponível na plataforma
        return None


_Backend: Any = _load_pty_backend()


def _default_shell() -> list[str]:
    """Comando do shell padrão da plataforma."""
    if _IS_WINDOWS:
        # Prefere pwsh (PowerShell 7) se disponível; senão cmd.exe.
        from shutil import which

        if which("pwsh.exe"):
            return ["pwsh.exe", "-NoLogo"]
        return ["cmd.exe"]
    shell = os.environ.get("SHELL", "/bin/bash")
    return [shell]


class PtySession:
    """Sessão de pseudo-terminal — wrapper async cross-platform.

    Use ``create()`` (não chame o construtor diretamente) para spawn assíncrono
    e arranque do read-loop. ``read()`` devolve bytes ou ``None`` quando o
    processo encerra.
    """

    #: Tamanho máximo do scrollback retido por sessão — limita memória por PTY
    #: de longa duração sem truncar o replay em reconexões normais.
    SCROLLBACK_MAX_BYTES = 64 * 1024

    def __init__(
        self,
        terminal_id: str,
        workspace_id: str,
        thread_id: str,
        proc: Any,
    ) -> None:
        self.terminal_id = terminal_id
        self.workspace_id = workspace_id
        self.thread_id = thread_id
        self._proc = proc
        # Fan-out: cada WS que abre este terminal ganha sua própria fila via
        # subscribe() — o read-loop faz broadcast do mesmo chunk pra todas as
        # filas registradas, garantindo que múltiplas abas no mesmo
        # terminal_id recebam a saída de forma independente.
        self._subscribers: list[asyncio.Queue[bytes | None]] = []
        # Buffer circular do output já emitido — permite que um WS que
        # reconecta (reload de página, troca de aba) receba o scroll-back
        # acumulado antes de passar a receber broadcast ao vivo.
        self._scrollback = bytearray()
        self._scrollback_start = 0
        self._next_cursor = 0
        self._input_ids: deque[str] = deque(maxlen=128)
        self._input_lock = threading.Lock()
        self._closed = False
        self._read_task: asyncio.Task | None = None

    # ── Factory ──────────────────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        *,
        terminal_id: str,
        workspace_id: str,
        thread_id: str,
        cwd: str,
        env: dict[str, str] | None = None,
        cols: int = 80,
        rows: int = 24,
        argv: list[str] | None = None,
        policy: SandboxPolicy | None = None,
    ) -> PtySession:
        if _Backend is None:
            raise RuntimeError(
                "Backend de PTY indisponível (instale pywinpty no Windows ou "
                "ptyprocess no Unix)."
            )

        cmd = argv or _default_shell()
        merged_env = {**os.environ, **(env or {})}

        # PTY interativo compartilha a MESMA política jailada
        # (backend.sandbox.workspace_jail) que `terminal`/`file_write`
        # daquela workspace: mesmos namespaces/rw_paths/ro_paths do bwrap.
        # Só `backend="local"` suporta PTY interativo sandboxado (docker/
        # ssh/modal exigiriam `-it`/`-tt`) — e só existe em Linux (bwrap).
        # Nunca cai silenciosamente pra shell sem proteção quando o usuário
        # configurou `[sandbox]`.
        if policy is not None and policy.enabled:
            if policy.backend != "local":
                raise RuntimeError(
                    f"Terminal interativo sandboxado ainda não suporta "
                    f"backend={policy.backend!r} — use a tool `terminal` "
                    "(comandos one-shot) ou desative [sandbox] pra essa "
                    "workspace."
                )
            from shutil import which

            if which("bwrap") is None:
                raise RuntimeError(
                    "bwrap não está disponível nesta plataforma — sandbox "
                    "indisponível para terminal interativo."
                )
            from backend.sandbox.dry_run import build_bwrap_command

            cmd = build_bwrap_command(policy, cwd, cmd)

        # pywinpty aceita lista ou string; ptyprocess espera argv (lista).
        try:
            proc = _Backend.spawn(
                cmd,
                dimensions=(rows, cols),
                cwd=cwd,
                env=merged_env,
            )
        except Exception as exc:
            logger.exception("pty_session: falha ao spawn %s", cmd)
            raise RuntimeError(f"Falha ao iniciar shell: {exc}") from exc

        session = cls(
            terminal_id=terminal_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
            proc=proc,
        )
        session._read_task = asyncio.create_task(
            session._read_loop(), name=f"pty-read-{terminal_id}"
        )
        logger.info(
            "pty_session: spawn %s (terminal=%s workspace=%s)",
            cmd[0],
            terminal_id,
            workspace_id,
        )
        return session

    # ── Read / Write / Resize / Close ────────────────────────────────────────

    async def _read_loop(self) -> None:
        loop = asyncio.get_event_loop()
        try:
            while not self._closed:
                try:
                    data = await loop.run_in_executor(
                        None, lambda: self._proc.read(4096)
                    )
                except EOFError:
                    break
                except Exception:
                    if self._closed:
                        return
                    logger.debug("pty_session: erro no read-loop %s", self.terminal_id)
                    break

                if not data:
                    break
                if isinstance(data, str):
                    data = data.encode("utf-8", errors="replace")
                try:
                    await self._broadcast(data)
                except asyncio.CancelledError:
                    return
        finally:
            await self._broadcast(None)

    async def _broadcast(self, data: bytes | None) -> None:
        if data is not None:
            self._scrollback.extend(data)
            self._next_cursor += len(data)
            overflow = len(self._scrollback) - self.SCROLLBACK_MAX_BYTES
            if overflow > 0:
                del self._scrollback[:overflow]
                self._scrollback_start = self._next_cursor - len(self._scrollback)
        for q in self._subscribers:
            with contextlib.suppress(asyncio.QueueFull):
                q.put_nowait(data)

    def subscribe(self) -> asyncio.Queue[bytes | None]:
        """Registra um novo consumidor — cada WS conectado a este terminal
        chama isso pra ganhar sua própria fila (broadcast, não round-robin).

        Se já há scroll-back acumulado (reconexão a uma sessão que já
        produziu output), o snapshot atual do buffer entra como primeiro
        item da fila — o consumidor lê o histórico antes de qualquer chunk
        ao vivo. A captura e o registro acontecem sem `await` entre eles,
        então nenhum broadcast concorrente pode intercalar duplicando ou
        pulando bytes.
        """
        q: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=4096)
        if self._scrollback:
            q.put_nowait(bytes(self._scrollback))
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[bytes | None]) -> None:
        with contextlib.suppress(ValueError):
            self._subscribers.remove(q)

    def read_since(self, cursor: int | None, max_bytes: int = 8192) -> dict[str, Any]:
        """Lê uma janela não consumidora do scrollback usando cursor absoluto.

        O cursor é a quantidade total de bytes emitidos pela sessão. Como o
        scrollback é limitado, um cursor antigo pode ficar fora da janela;
        nesse caso ``truncated`` informa ao consumidor que houve perda do
        prefixo, sem alterar o fluxo de broadcast para os WebSockets.
        """
        limit = int(max_bytes)
        if limit < 1:
            raise ValueError("max_bytes deve ser positivo")
        limit = min(limit, self.SCROLLBACK_MAX_BYTES)
        requested = self._scrollback_start if cursor is None else int(cursor)
        if requested < 0 or requested > self._next_cursor:
            raise ValueError("cursor inválido")
        truncated = requested < self._scrollback_start
        start = max(requested, self._scrollback_start)
        offset = start - self._scrollback_start
        chunk = bytes(self._scrollback[offset : offset + limit])
        next_cursor = start + len(chunk)
        return {
            "data": chunk,
            "cursor": next_cursor,
            "alive": self.is_alive() and not self._closed,
            "truncated": truncated,
            "has_more": next_cursor < self._next_cursor,
        }

    def write(self, data: bytes) -> bool:
        if self._closed:
            return False
        try:
            if _IS_WINDOWS:
                # pywinpty espera str
                self._proc.write(data.decode("utf-8", errors="replace"))
            else:
                self._proc.write(data)
            return True
        except Exception:
            logger.debug("pty_session: write falhou %s", self.terminal_id)
            return False

    def write_input(self, data: bytes, request_id: str) -> dict[str, Any]:
        """Escreve uma entrada idempotente e devolve estado observável."""
        if not request_id:
            return {"status": "error", "code": "request_id_required"}
        with self._input_lock:
            if request_id in self._input_ids:
                return {"status": "duplicate", "terminal_id": self.terminal_id}
            if not self.is_alive() or self._closed:
                return {
                    "status": "error",
                    "code": "closed",
                    "terminal_id": self.terminal_id,
                }
            if not self.write(data):
                return {
                    "status": "error",
                    "code": "write_failed",
                    "terminal_id": self.terminal_id,
                }
            self._input_ids.append(request_id)
            return {"status": "accepted", "terminal_id": self.terminal_id}

    def resize(self, cols: int, rows: int) -> None:
        try:
            self._proc.setwinsize(rows, cols)
        except Exception:
            logger.debug("pty_session: resize falhou %s", self.terminal_id)

    def is_alive(self) -> bool:
        try:
            return bool(self._proc.isalive())
        except Exception:
            return False

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._proc.terminate(force=True)
        except Exception:
            logger.debug("pty_session: terminate falhou %s", self.terminal_id)
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
        logger.info("pty_session: encerrado %s", self.terminal_id)
