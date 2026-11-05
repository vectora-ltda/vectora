"""Seleção efêmera e verificação de janelas para ``computer_use``.

O adaptador mantém handles nativos somente em memória e nunca os expõe ao
agente. Cada seleção fica vinculada ao usuário, workspace e thread que a
criaram; reiniciar o processo invalida todas as seleções.
"""

from __future__ import annotations

import logging
import platform
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

logger = logging.getLogger(__name__)

_SENSITIVE_TITLE = re.compile(
    r"(?i)(password|senha|token|secret|credential|api[ _-]?key)"
)
_MAX_TITLE = 96
_SELECTION_TTL_SECONDS = 15 * 60
_ACTION_WINDOW_SECONDS = 60.0
_MAX_ACTIONS_PER_WINDOW = 30


@dataclass(slots=True, frozen=True)
class WindowInfo:
    """Metadados mínimos e seguros de uma janela elegível."""

    window_id: str
    title: str
    left: int
    top: int
    width: int
    height: int
    visible: bool


@dataclass(slots=True)
class _Selection:
    window_id: str
    native: Any
    info: WindowInfo
    created_at: float


class DesktopWindowRegistry:
    """Resolve janelas apenas dentro do contexto efêmero que as descobriu."""

    def __init__(self) -> None:
        self._candidates: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._selections: dict[tuple[str, str, str], _Selection] = {}
        self._action_times: dict[tuple[str, str, str], list[float]] = {}
        self._action_lock = threading.Lock()

    @staticmethod
    def _scope(
        user_id: str, workspace_id: str, thread_id: str
    ) -> tuple[str, str, str] | None:
        if not user_id or not workspace_id or not thread_id:
            return None
        return user_id, workspace_id, thread_id

    @staticmethod
    def _adapter() -> Any:
        if platform.system() != "Windows":
            return None
        try:
            import pygetwindow

            return pygetwindow
        except Exception:
            return None

    @staticmethod
    def _safe_title(value: str) -> str:
        title = value.strip()[:_MAX_TITLE]
        return "[REDACTED]" if _SENSITIVE_TITLE.search(title) else title

    @classmethod
    def capabilities(cls) -> dict[str, Any]:
        return {
            "platform": platform.system().lower(),
            "window_control": cls._adapter() is not None,
            "reason": None if cls._adapter() is not None else "platform_unavailable",
        }

    def list_windows(
        self, *, user_id: str, workspace_id: str, thread_id: str
    ) -> list[WindowInfo]:
        scope = self._scope(user_id, workspace_id, thread_id)
        if scope is None:
            return []
        adapter = self._adapter()
        if adapter is None:
            raise RuntimeError("controle de janelas indisponível nesta plataforma")
        candidates: dict[str, Any] = {}
        result: list[WindowInfo] = []
        for native in adapter.getAllWindows():
            try:
                left, top = int(native.left), int(native.top)
                width, height = int(native.width), int(native.height)
                visible = bool(width > 0 and height > 0 and not native.isMinimized)
                title = self._safe_title(str(native.title))
                if not visible or not title:
                    continue
                window_id = uuid4().hex
                info = WindowInfo(window_id, title, left, top, width, height, visible)
                candidates[window_id] = native
                result.append(info)
            except Exception as exc:
                logger.debug(
                    "desktop_windows: janela ignorada", extra={"error": str(exc)}
                )
                continue
        self._candidates[scope] = candidates
        return result

    def select(
        self, *, user_id: str, workspace_id: str, thread_id: str, window_id: str
    ) -> WindowInfo:
        scope = self._scope(user_id, workspace_id, thread_id)
        if scope is None or not window_id:
            raise ValueError("contexto e window_id são obrigatórios")
        native = self._candidates.get(scope, {}).get(window_id)
        if native is None:
            raise LookupError("janela não encontrada nesta sessão")
        info = self._inspect(window_id, native)
        self._selections[scope] = _Selection(window_id, native, info, time.monotonic())
        return info

    def selected(
        self, *, user_id: str, workspace_id: str, thread_id: str
    ) -> _Selection:
        scope = self._scope(user_id, workspace_id, thread_id)
        if scope is None or scope not in self._selections:
            raise LookupError("nenhuma janela selecionada nesta sessão")
        selection = self._selections[scope]
        if time.monotonic() - selection.created_at > _SELECTION_TTL_SECONDS:
            del self._selections[scope]
            raise LookupError("seleção de janela expirada")
        selection.info = self._inspect(selection.window_id, selection.native)
        return selection

    def _inspect(self, window_id: str, native: Any) -> WindowInfo:
        try:
            left, top = int(native.left), int(native.top)
            width, height = int(native.width), int(native.height)
            visible = bool(width > 0 and height > 0 and not native.isMinimized)
            if not visible:
                raise LookupError("janela não está visível")
            return WindowInfo(
                window_id,
                self._safe_title(str(native.title)),
                left,
                top,
                width,
                height,
                visible,
            )
        except LookupError:
            raise
        except Exception as exc:
            raise LookupError("janela indisponível") from exc

    def require_focus(self, selection: _Selection) -> WindowInfo:
        adapter = self._adapter()
        if adapter is None:
            raise RuntimeError("controle de janelas indisponível nesta plataforma")
        info = self._inspect(selection.window_id, selection.native)
        active = adapter.getActiveWindow()
        active_id = getattr(active, "_hWnd", getattr(active, "hwnd", None))
        selected_id = getattr(
            selection.native, "_hWnd", getattr(selection.native, "hwnd", None)
        )
        if active_id != selected_id:
            raise PermissionError("foco da janela selecionada foi perdido")
        return info

    def allow_action(self, *, user_id: str, workspace_id: str, thread_id: str) -> bool:
        """Applies a small per-session input/capture rate limit."""
        scope = self._scope(user_id, workspace_id, thread_id)
        if scope is None:
            return False
        now = time.monotonic()
        with self._action_lock:
            recent = [
                timestamp
                for timestamp in self._action_times.get(scope, [])
                if now - timestamp < _ACTION_WINDOW_SECONDS
            ]
            if len(recent) >= _MAX_ACTIONS_PER_WINDOW:
                self._action_times[scope] = recent
                return False
            recent.append(now)
            self._action_times[scope] = recent
        return True

    def invalidate(self, thread_id: str) -> None:
        """Descarta candidatos, seleção e limites da thread encerrada."""
        for mapping in (self._candidates, self._selections, self._action_times):
            for scope in [key for key in mapping if key[2] == thread_id]:
                mapping.pop(scope, None)

    def focus(self, selection: _Selection) -> WindowInfo:
        self._inspect(selection.window_id, selection.native)
        selection.native.activate()
        return self.require_focus(selection)


desktop_window_registry = DesktopWindowRegistry()


def window_media_dir(thread_id: str) -> Path:
    return (
        Path.home() / ".vectora" / "artifacts" / (thread_id or "sem-sessao") / "media"
    )


__all__ = [
    "DesktopWindowRegistry",
    "WindowInfo",
    "desktop_window_registry",
    "window_media_dir",
]
