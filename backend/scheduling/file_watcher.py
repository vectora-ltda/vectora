"""File Watcher.

1 watcher por workspace: usa watchdog para detectar mudanças em disco
com debounce 300ms. Publica evento de invalidação via KV pub/sub para que
o SSE notifique o frontend e invalide os tabs (files, git).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)

_IGNORE_PATTERNS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".mypy_cache",
    ".ruff_cache",
}

# Singleton registry: workspace_id → FileWatcher
_registry: dict[str, FileWatcher] = {}


async def debounce_collect(
    queue: asyncio.Queue,
    callback: Callable[[set], Coroutine[Any, Any, None]],
    debounce_ms: int = 300,
) -> None:
    """Lê eventos da queue com debounce.

    Acumula todos os paths recebidos durante ``debounce_ms`` milissegundos
    de silêncio e então chama ``callback`` com o conjunto acumulado.
    """
    debounce_s = debounce_ms / 1000.0
    pending: set[str] = set()

    while True:
        try:
            path = await asyncio.wait_for(queue.get(), timeout=debounce_s)
            pending.add(path)
        except TimeoutError:
            if pending:
                batch = pending.copy()
                pending.clear()
                try:
                    await callback(batch)
                except Exception:
                    logger.exception("debounce_collect: callback falhou")


async def _kv_publish(workspace_id: str, channel: str, payload: dict) -> None:
    """Publica via KV (substituível em testes via monkeypatch)."""
    try:
        import json

        from backend.persistence.kv import get_kv

        kv = await get_kv()
        await kv.set(f"watch:{channel}:{workspace_id}", json.dumps(payload), ttl_s=10)
    except Exception:
        logger.debug("file_watcher: falha ao publicar no KV workspace=%s", workspace_id)


class FileWatcher:
    """Monitora um diretório de workspace e publica invalidações via KV."""

    def __init__(self, workspace_path: str, workspace_id: str) -> None:
        self._path = workspace_path
        self._workspace_id = workspace_id
        self._queue: asyncio.Queue = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._observer: Any = None
        self.running = False

    async def start(self) -> None:
        """Inicia o watcher (observer + debounce loop)."""
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(
            debounce_collect(self._queue, self._on_changes)
        )
        try:
            self._observer = await asyncio.to_thread(self._start_observer)
        except Exception as exc:
            logger.warning(
                "file_watcher: falha ao iniciar watchdog para %s: %s",
                self._path,
                exc,
            )

    def _start_observer(self) -> Any:
        """Inicia o Observer do watchdog em thread separada."""
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer

            queue = self._queue
            ignore = _IGNORE_PATTERNS

            class _Handler(FileSystemEventHandler):
                def on_any_event(self, event: Any) -> None:
                    if event.is_directory:
                        return
                    src = getattr(event, "src_path", "") or ""
                    if any(pat in src for pat in ignore):
                        return
                    with contextlib.suppress(asyncio.QueueFull):
                        queue.put_nowait(src)

            observer = Observer()
            observer.schedule(_Handler(), self._path, recursive=True)
            observer.start()
            return observer
        except ImportError:
            logger.warning("file_watcher: watchdog não instalado — watcher desativado")
            return None

    async def stop(self) -> None:
        """Para o watcher."""
        self.running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._observer is not None:
            try:
                await asyncio.to_thread(self._observer.stop)
                await asyncio.to_thread(self._observer.join)
            except Exception:
                pass

    async def _on_changes(self, paths: set) -> None:
        """Publica evento de invalidação quando arquivos mudam."""
        logger.debug(
            "file_watcher: %d arquivo(s) alterado(s) em workspace=%s",
            len(paths),
            self._workspace_id,
        )
        payload = {
            "workspace_id": self._workspace_id,
            "tabs": ["files", "git"],
            "changed_paths": list(paths),
        }
        await _kv_publish(self._workspace_id, "vectora:files_changed", payload)


def get_watcher(workspace_path: str, workspace_id: str) -> FileWatcher:
    """Retorna o FileWatcher singleton para este workspace_id."""
    if workspace_id not in _registry:
        _registry[workspace_id] = FileWatcher(workspace_path, workspace_id)
    return _registry[workspace_id]
