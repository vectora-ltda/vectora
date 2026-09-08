"""Controle de mouse/teclado da tela do desktop — a tool de maior risco.

Diferente de toda outra tool do produto, `computer_use` age fora do sandbox
de arquivo/terminal: um clique errado é irreversível (não existe
`git checkout` pra desfazer ter clicado no botão errado). Por isso duas
proteções que nenhuma outra tool tem:

- **Opt-in explícito por workspace** (`[computer_use] enabled = true` em
  `vectora.toml`) — sem a seção, a tool recusa antes de tocar em qualquer
  coisa. Nunca liga silenciosamente.
- **Aprovação humana sempre**, mesmo em `permission_mode="bypass"` — ver
  `_mode_should_interrupt` em `backend/engine/hitl.py`, que abre
  uma exceção só pra esta tool.

A biblioteca de automação é ``pyautogui-next`` (fork mantido do PyAutoGUI
original, mesmo import ``pyautogui`` — o original está sem release desde
2022). As chamadas de `pyautogui` são síncronas/bloqueantes (I/O de tela) —
rodadas via `asyncio.to_thread` pra nunca travar o event loop (CLAUDE.md
regra 10).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from backend.services.desktop_windows import (
    DesktopWindowRegistry,
    WindowInfo,
    desktop_window_registry,
    window_media_dir,
)
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)

_ACOES_VALIDAS = frozenset({"screenshot", "click", "type_text"})
_MAX_TEXT_BYTES = 4096


def _computer_use_enabled_for_cwd(cwd: str) -> bool:
    """Lê `[computer_use]` de `vectora.toml` no `cwd` dado.

    Ausência de arquivo/seção é `False` — `load_workspace_config` já
    degrada pra `None`/defaults em qualquer erro de I/O ou parse."""
    from backend.workspace.workspace_config import load_workspace_config

    config = load_workspace_config(cwd)
    return bool(config and config.computer_use.enabled)


def _computer_use_enabled(workspace_id: str) -> bool:
    """Resolve o opt-in a partir do `workspace_id` do contexto da tool.

    Fail-closed: workspace desconhecida ou qualquer erro ao resolver o
    `cwd` volta `False` — nunca assume habilitado por omissão."""
    if not workspace_id:
        return False
    try:
        from backend.workspace.workspace import workspace_registry

        ws = workspace_registry.get(workspace_id)
        cwd = getattr(ws, "cwd", None)
        return bool(cwd) and _computer_use_enabled_for_cwd(str(cwd))
    except Exception:
        logger.debug(
            "computer_use: falha ao checar opt-in da workspace %s", workspace_id
        )
        return False


def _media_dir(session_id: str) -> Path:
    return (
        Path.home() / ".vectora" / "artifacts" / (session_id or "sem-sessao") / "media"
    )


def _take_screenshot_sync(region: tuple[int, int, int, int] | None = None) -> bytes:
    import io

    import pyautogui

    buffer = io.BytesIO()
    pyautogui.screenshot(region=region).save(buffer, format="PNG")
    return buffer.getvalue()


def _click_sync(x: int, y: int) -> None:
    import pyautogui

    pyautogui.click(x=x, y=y)


def _type_text_sync(text: str) -> None:
    import pyautogui

    pyautogui.typewrite(text)


async def _audit_event(
    ctx: ToolContext, action: str, *, success: bool, window_id: str = ""
) -> None:
    """Registra apenas metadados seguros; conteúdo e imagem nunca entram no audit."""
    try:
        from backend.rbac.auth import get_db_for_audit, write_audit

        db = await get_db_for_audit()
        await write_audit(
            db,
            ctx.user_id,
            f"computer_use.{action}",
            success=success,
            metadata={
                "workspace_id": ctx.workspace_id,
                "thread_id": ctx.thread_id,
                "window_id": window_id,
                "action": action,
            },
            target_type="desktop_window",
            target_id=window_id or None,
        )
    except Exception:
        logger.debug("computer_use: auditoria indisponível", extra={"action": action})


def _window_payload(info: WindowInfo) -> dict[str, object]:
    return {
        "window_id": info.window_id,
        "title": info.title,
        "geometry": {"width": info.width, "height": info.height},
        "visible": info.visible,
    }


@vtool(
    extras=ToolExtras(
        render_hint="json", category="computer_use", destructive=False, icon="monitor"
    )
)
async def list_desktop_windows(ctx: ToolContext) -> str:
    """Lista janelas locais elegíveis sem capturar conteúdo ou aceitar handles."""
    try:
        if not _computer_use_enabled(ctx.workspace_id):
            return json.dumps({"status": "error", "code": "opt_in_required"})
        windows = await asyncio.to_thread(
            desktop_window_registry.list_windows,
            user_id=ctx.user_id,
            workspace_id=ctx.workspace_id,
            thread_id=ctx.thread_id,
        )
        await _audit_event(ctx, "list_windows", success=True)
        return json.dumps(
            {
                "status": "ok",
                "capabilities": DesktopWindowRegistry.capabilities(),
                "windows": [_window_payload(window) for window in windows],
            },
            ensure_ascii=False,
        )
    except Exception:
        await _audit_event(ctx, "list_windows", success=False)
        return json.dumps({"status": "error", "code": "platform_unavailable"})


@vtool(
    extras=ToolExtras(
        render_hint="json", category="computer_use", destructive=True, icon="monitor"
    )
)
async def select_desktop_window(window_id: str, ctx: ToolContext) -> str:
    """Seleciona uma janela descoberta no contexto atual, sem aceitar handles."""
    try:
        if not _computer_use_enabled(ctx.workspace_id):
            return json.dumps({"status": "error", "code": "opt_in_required"})
        info = await asyncio.to_thread(
            desktop_window_registry.select,
            user_id=ctx.user_id,
            workspace_id=ctx.workspace_id,
            thread_id=ctx.thread_id,
            window_id=window_id,
        )
        await _audit_event(ctx, "select_window", success=True, window_id=info.window_id)
        return json.dumps(
            {"status": "selected", **_window_payload(info)}, ensure_ascii=False
        )
    except Exception:
        await _audit_event(ctx, "select_window", success=False, window_id=window_id)
        return json.dumps({"status": "error", "code": "window_unavailable"})


@vtool(
    extras=ToolExtras(
        render_hint="json", category="computer_use", destructive=True, icon="monitor"
    )
)
async def focus_desktop_window(ctx: ToolContext) -> str:
    """Solicita foco e confirma a janela previamente selecionada."""
    try:
        if not _computer_use_enabled(ctx.workspace_id):
            return json.dumps({"status": "error", "code": "opt_in_required"})
        selection = desktop_window_registry.selected(
            user_id=ctx.user_id, workspace_id=ctx.workspace_id, thread_id=ctx.thread_id
        )
        info = await asyncio.to_thread(desktop_window_registry.focus, selection)
        await _audit_event(ctx, "focus_window", success=True, window_id=info.window_id)
        return json.dumps(
            {"status": "focused", **_window_payload(info)}, ensure_ascii=False
        )
    except PermissionError:
        await _audit_event(ctx, "focus_lost", success=False)
        return json.dumps({"status": "error", "code": "focus_lost"})
    except Exception:
        await _audit_event(ctx, "focus_window", success=False)
        return json.dumps({"status": "error", "code": "window_unavailable"})


@vtool(
    extras=ToolExtras(
        render_hint="json",
        category="computer_use",
        destructive=True,
        icon="monitor",
    )
)
async def computer_use(
    action: str,
    ctx: ToolContext,
    x: int | None = None,
    y: int | None = None,
    text: str = "",
) -> str:
    """Controla mouse/teclado da tela do desktop — `screenshot`, `click`, `type_text`.

    Ação física na máquina do usuário, fora do sandbox de arquivo/terminal.
    Só existe quando o workspace tem `[computer_use] enabled = true` em
    `vectora.toml`, e sempre pede aprovação humana antes de executar,
    mesmo em modo `bypass`.

    Args:
        action: `screenshot` (captura a tela), `click` (requer `x`/`y`) ou
            `type_text` (requer `text`).
        x: Coordenada X do clique, obrigatória para `click`.
        y: Coordenada Y do clique, obrigatória para `click`.
        text: Texto a digitar, obrigatório para `type_text`.

    Returns:
        JSON com o resultado da ação, ou com `error`.
    """
    try:
        if not _computer_use_enabled(ctx.workspace_id):
            return json.dumps(
                {
                    "error": (
                        "computer_use está desligada neste workspace — "
                        "adicione `[computer_use]\\nenabled = true` ao "
                        "vectora.toml pra habilitar"
                    )
                },
                ensure_ascii=False,
            )

        if action not in _ACOES_VALIDAS:
            return json.dumps(
                {"error": f"ação desconhecida: {action!r}"}, ensure_ascii=False
            )

        selection = desktop_window_registry.selected(
            user_id=ctx.user_id, workspace_id=ctx.workspace_id, thread_id=ctx.thread_id
        )
        allowed = await asyncio.to_thread(
            desktop_window_registry.allow_action,
            user_id=ctx.user_id,
            workspace_id=ctx.workspace_id,
            thread_id=ctx.thread_id,
        )
        if not allowed:
            await _audit_event(
                ctx, "rate_limited", success=False, window_id=selection.window_id
            )
            return json.dumps({"status": "error", "code": "rate_limited"})
        info = await asyncio.to_thread(desktop_window_registry.require_focus, selection)

        if action == "screenshot":
            data = await asyncio.to_thread(
                _take_screenshot_sync, (info.left, info.top, info.width, info.height)
            )
            directory = window_media_dir(ctx.thread_id)
            directory.mkdir(parents=True, exist_ok=True)
            path = (
                directory / f"{datetime.now(UTC):%Y%m%d-%H%M%S}-{uuid4().hex[:8]}.png"
            )
            path.write_bytes(data)
            await _audit_event(
                ctx, "screenshot", success=True, window_id=info.window_id
            )
            return json.dumps(
                {"status": "ok", "action": "screenshot", "path": f"media/{path.name}"},
                ensure_ascii=False,
            )

        if action == "click":
            if (
                x is None
                or y is None
                or x < 0
                or y < 0
                or x >= info.width
                or y >= info.height
            ):
                return json.dumps({"error": "click exige x e y"}, ensure_ascii=False)
            await asyncio.to_thread(desktop_window_registry.require_focus, selection)
            await asyncio.to_thread(_click_sync, info.left + x, info.top + y)
            await _audit_event(ctx, "click", success=True, window_id=info.window_id)
            return json.dumps(
                {"status": "ok", "action": "click", "x": x, "y": y}, ensure_ascii=False
            )

        if not text:
            return json.dumps(
                {"error": "type_text exige texto não vazio"}, ensure_ascii=False
            )
        if len(text.encode("utf-8")) > _MAX_TEXT_BYTES:
            return json.dumps({"status": "error", "code": "text_too_large"})
        await asyncio.to_thread(desktop_window_registry.require_focus, selection)
        await asyncio.to_thread(_type_text_sync, text)
        await _audit_event(ctx, "type_text", success=True, window_id=info.window_id)
        return json.dumps({"status": "ok", "action": "type_text"}, ensure_ascii=False)
    except PermissionError:
        logger.warning("computer_use: foco perdido", extra={"action": action})
        await _audit_event(ctx, "focus_lost", success=False)
        return json.dumps({"status": "error", "code": "focus_lost"})
    except LookupError:
        await _audit_event(ctx, "window_unavailable", success=False)
        return json.dumps({"status": "error", "code": "window_unavailable"})
    except Exception:
        logger.exception("computer_use: falha", extra={"action": action})
        await _audit_event(ctx, action, success=False)
        return json.dumps({"status": "error", "code": "platform_failure"})
