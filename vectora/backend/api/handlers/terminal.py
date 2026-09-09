"""Handler do terminal embarcado.

WebSocket:
    /vectora.terminal.v1/ws?thread_id=...&workspace_id=...&terminal_id=...&token=...

REST auxiliar:
    GET    /vectora.terminal.v1/list?thread_id=...   — terminais ativos da sessão
    POST   /vectora.terminal.v1/close                — encerra um terminal

A autenticação do WS é feita **dentro** do handler (BaseHTTPMiddleware não cobre
WebSocket): o ``token`` na query é validado com ``decode_access_token``. A
abertura só prossegue em workspace ``trusted=True`` — o terminal é um shell
sem sandbox, coerente com o princípio "quem tem shell no servidor tem root".
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, Request, WebSocket
from fastapi.websockets import WebSocketDisconnect
from pydantic import BaseModel

from backend.api.middleware.auth import _auth_enabled
from backend.services.pty_registry import pty_registry
from backend.services.pty_session import PtySession

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/vectora.terminal.v1", tags=["terminal"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _user_from_token(token: str) -> Any:
    if not token:
        return None
    try:
        from backend.rbac.auth import decode_access_token, get_user_by_id

        payload = decode_access_token(token)
        uid = str(payload.get("sub", ""))
        if not uid:
            return None
        return await get_user_by_id(uid)
    except Exception:
        return None


def _trusted_workspace(workspace_id: str) -> Any:
    from backend.workspace.workspace import workspace_registry

    ws = workspace_registry.get(workspace_id) if workspace_id else None
    if ws is None or not getattr(ws, "trusted", False):
        return None
    return ws


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------


@router.websocket("/ws")
async def terminal_ws(ws: WebSocket) -> None:
    """Pipe bidirecional entre o PTY do servidor e o xterm.js no browser."""
    await ws.accept()

    qs = ws.query_params
    token = qs.get("token", "")
    thread_id = qs.get("thread_id", "")
    workspace_id = qs.get("workspace_id", "")
    terminal_id = qs.get("terminal_id") or uuid.uuid4().hex[:12]

    # Auth — token na query (cookies não trafegam bem em WS cross-origin)
    user = await _user_from_token(token)
    if _auth_enabled() and user is None:
        await ws.send_text(
            json.dumps({"type": "error", "message": "not authenticated"})
        )
        await ws.close(code=1008)
        return

    workspace = _trusted_workspace(workspace_id)
    if workspace is None:
        await ws.send_text(
            json.dumps({"type": "error", "message": "workspace não confiável"})
        )
        await ws.close(code=1008)
        return

    # Sem isso, um usuário autenticado abria terminal interativo (execução
    # arbitrária) no workspace de outro usuário só sabendo o id.
    owner_id = getattr(workspace, "owner_id", None)
    if owner_id is not None and user is not None:
        role = str(getattr(user, "role", "")).lower()
        if role not in {"root", "admin"} and str(getattr(user, "id", "")) != owner_id:
            await ws.send_text(
                json.dumps(
                    {"type": "error", "message": "acesso negado a este workspace"}
                )
            )
            await ws.close(code=1008)
            return

    # Terminal interativo só roda em transport "local" — PTY remoto
    # (SSH/Codespace) usa a tool `terminal` (comandos one-shot) em vez
    # deste WebSocket.
    transport = str(getattr(workspace, "transport", "local"))
    if transport != "local":
        await ws.send_text(
            json.dumps(
                {
                    "type": "error",
                    "message": (
                        f"Terminal interativo ainda não disponível em "
                        f"workspace transport={transport!r}. Use a tool "
                        "`terminal` no chat (comandos one-shot)."
                    ),
                }
            )
        )
        await ws.close(code=1011)
        return

    session = pty_registry.get(terminal_id)
    if session is not None and (
        getattr(session, "user_id", "local") != str(getattr(user, "id", "local"))
        or session.thread_id != thread_id
        or session.workspace_id != workspace_id
    ):
        await ws.send_text(
            json.dumps({"type": "error", "message": "acesso negado ao terminal"})
        )
        await ws.close(code=1008)
        return
    if session is None or not session.is_alive():
        try:
            from pathlib import Path

            from backend.sandbox.policy import parse_policy

            policy = parse_policy(Path(workspace.cwd) / "vectora.toml")
            session = PtySession.create(
                terminal_id=terminal_id,
                workspace_id=workspace_id,
                thread_id=thread_id,
                user_id=str(getattr(user, "id", "local")),
                cwd=workspace.cwd,
                policy=policy,
            )
            pty_registry.add(session)
        except Exception as exc:
            await ws.send_text(json.dumps({"type": "error", "message": str(exc)}))
            await ws.close(code=1011)
            return

    await ws.send_text(json.dumps({"type": "connected", "terminal_id": terminal_id}))

    # PTY → WS pump. subscribe() dá a esta conexão sua própria fila — se
    # outra aba já tiver o mesmo terminal_id aberto, as duas recebem cada
    # chunk (broadcast), nenhuma "rouba" o output da outra.
    out_queue = session.subscribe()

    async def pump_out() -> None:
        try:
            while True:
                data = await out_queue.get()
                if data is None:
                    break
                await ws.send_bytes(data)
        except WebSocketDisconnect:
            return
        except Exception:
            logger.debug("terminal: erro no pump_out %s", terminal_id)
        finally:
            session.unsubscribe(out_queue)
        with contextlib.suppress(Exception):
            await ws.send_text(json.dumps({"type": "closed"}))

    out_task = asyncio.create_task(pump_out(), name=f"pty-out-{terminal_id}")

    try:
        while True:
            msg = await ws.receive()
            mtype = msg.get("type")
            if mtype == "websocket.disconnect":
                break
            # Bytes do browser → stdin do PTY
            data = msg.get("bytes")
            if data:
                session.write(data)
                continue
            # Mensagens de controle JSON (resize / close)
            text = msg.get("text")
            if text:
                try:
                    ctrl = json.loads(text)
                except Exception:
                    logger.debug("terminal: JSON inválido descartado %s", terminal_id)
                    continue
                ctype = ctrl.get("type")
                if ctype == "resize":
                    session.resize(int(ctrl.get("cols", 80)), int(ctrl.get("rows", 24)))
                elif ctype == "stdin":
                    # Fallback: alguns clientes enviam input como texto.
                    session.write(str(ctrl.get("data", "")).encode("utf-8"))
                elif ctype == "close":
                    pty_registry.close(terminal_id)
                    break
    except WebSocketDisconnect:
        # Mantém a PTY viva — usuário pode reconectar com o mesmo terminal_id.
        pass
    finally:
        out_task.cancel()


# ---------------------------------------------------------------------------
# REST auxiliar
# ---------------------------------------------------------------------------


class CloseBody(BaseModel):
    terminal_id: str
    thread_id: str
    workspace_id: str


def _request_user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    user_id = str(getattr(user, "id", "") or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="Autenticação necessária.")
    return user_id


@router.get("/list")
async def list_terminals(request: Request, thread_id: str, workspace_id: str) -> dict:
    """Lista os terminais ativos da sessão (do usuário autenticado)."""
    user_id = _request_user_id(request)
    if not thread_id or not workspace_id:
        raise HTTPException(status_code=404, detail="Contexto não encontrado.")
    sessions = pty_registry.list_for_context(
        user_id=user_id, thread_id=thread_id, workspace_id=workspace_id
    )
    return {
        "terminals": [
            {
                "terminal_id": s.terminal_id,
                "thread_id": s.thread_id,
                "workspace_id": s.workspace_id,
                "alive": s.is_alive(),
            }
            for s in sessions
        ]
    }


@router.post("/close")
async def close_terminal(body: CloseBody, request: Request) -> dict:
    user_id = _request_user_id(request)
    if (
        pty_registry.resolve_for_context(
            body.terminal_id,
            user_id=user_id,
            thread_id=body.thread_id,
            workspace_id=body.workspace_id,
        )
        is None
    ):
        raise HTTPException(status_code=404, detail="Terminal não encontrado.")
    pty_registry.close(body.terminal_id)
    return {"status": "closed", "terminal_id": body.terminal_id}
