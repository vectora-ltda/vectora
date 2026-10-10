"""Tools de gestão dos terminais PTY abertos manualmente pelo usuário via WebSocket.

Reaproveita o mesmo ``pty_registry`` que o handler REST
(``backend/api/handlers/terminal.py``) usa — não duplica o tracking de sessões.
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path

from backend.services.pty_registry import pty_registry
from backend.services.pty_session import PtySession
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)
_DEFAULT_CONTEXT = ToolContext()


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="filesystem",
        destructive=True,
        icon="terminal",
    )
)
async def open_terminal(
    cols: int = 120,
    rows: int = 32,
    ctx: ToolContext = _DEFAULT_CONTEXT,
) -> str:
    """Abre uma nova PTY do agente, visível como uma aba na Workbench.

    A sessão é registrada no mesmo ``pty_registry`` usado pelo WebSocket da
    Workbench. Assim o usuário pode acompanhar e assumir qualquer quantidade
    de terminais abertos pelo agente.
    """
    try:
        from backend.sandbox.policy import parse_policy
        from backend.workspace.workspace import workspace_registry

        workspace = workspace_registry.get(ctx.workspace_id)
        if workspace is None or not getattr(workspace, "trusted", False):
            return json.dumps(
                {"status": "error", "code": "workspace_not_trusted"},
                ensure_ascii=False,
            )
        if not ctx.thread_id or not ctx.workspace_id:
            return json.dumps(
                {"status": "error", "code": "invalid_context"},
                ensure_ascii=False,
            )
        terminal_id = f"agent-{uuid.uuid4().hex[:12]}"
        policy = parse_policy(Path(workspace.cwd) / "vectora.toml")
        session = PtySession.create(
            terminal_id=terminal_id,
            workspace_id=ctx.workspace_id,
            thread_id=ctx.thread_id,
            user_id=ctx.user_id,
            cwd=workspace.cwd,
            cols=max(20, min(int(cols), 400)),
            rows=max(5, min(int(rows), 200)),
            policy=policy,
        )
        pty_registry.add(session)
        logger.info(
            "terminal_agent_opened",
            extra={
                "terminal_id": terminal_id,
                "thread_id": ctx.thread_id,
                "workspace_id": ctx.workspace_id,
            },
        )
        return json.dumps(
            {
                "status": "opened",
                "terminal_id": terminal_id,
                "shared": True,
                "message": "PTY aberta na Workbench; use write_terminal/read_terminal.",
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.exception(
            "terminal_agent_open_failed",
            extra={"thread_id": ctx.thread_id, "workspace_id": ctx.workspace_id},
        )
        return json.dumps(
            {"status": "error", "code": "open_failed", "message": str(exc)},
            ensure_ascii=False,
        )


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="filesystem",
        destructive=False,
        icon="terminal",
    )
)
async def list_terminals(ctx: ToolContext) -> str:
    """Lista os terminais PTY abertos nesta sessão (incluindo os que podem ser assumidos)."""
    sessions = pty_registry.list_for_user_workspace(
        user_id=ctx.user_id, workspace_id=ctx.workspace_id
    )
    return json.dumps(
        {
            "terminals": [
                {
                    "terminal_id": s.terminal_id,
                    "thread_id": s.thread_id,
                    "workspace_id": s.workspace_id,
                    "user_id": getattr(s, "user_id", "local"),
                    "alive": s.is_alive(),
                    "is_current_thread": s.thread_id == ctx.thread_id,
                }
                for s in sessions
            ]
        }
    )


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="filesystem",
        destructive=False,
        icon="terminal",
    )
)
async def attach_terminal(terminal_id: str, ctx: ToolContext = _DEFAULT_CONTEXT) -> str:
    """Assume explicitamente o controle de um terminal PTY interativo aberto pelo usuário.

    Isso vincula o terminal à thread atual do agente, permitindo chamar read_terminal e write_terminal nele.
    """
    if not terminal_id:
        return json.dumps({"status": "error", "message": "terminal_id é obrigatório."})
    if not ctx.thread_id or not ctx.workspace_id:
        return json.dumps(
            {"status": "error", "message": "Contexto inválido para claim."}
        )

    session = pty_registry.claim_for_context(
        terminal_id,
        user_id=ctx.user_id,
        workspace_id=ctx.workspace_id,
        thread_id=ctx.thread_id,
    )
    if session is None:
        return json.dumps(
            {
                "status": "error",
                "message": f"Terminal {terminal_id!r} não encontrado ou incompatível.",
            }
        )
    return json.dumps({"status": "attached", "terminal_id": terminal_id})


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="filesystem",
        destructive=True,
        icon="terminal",
    )
)
async def close_terminal(terminal_id: str, ctx: ToolContext = _DEFAULT_CONTEXT) -> str:
    """Encerra um terminal PTY aberto manualmente pelo usuário, pelo id."""
    if not terminal_id:
        return json.dumps({"status": "error", "message": "terminal_id é obrigatório."})
    session = pty_registry.get(terminal_id)
    if session is None or (
        ctx is not _DEFAULT_CONTEXT
        and pty_registry.resolve_for_context(
            terminal_id,
            user_id=ctx.user_id,
            thread_id=ctx.thread_id,
            workspace_id=ctx.workspace_id,
        )
        is None
    ):
        return json.dumps({"status": "error", "code": "not_found"})
    if not pty_registry.close(terminal_id):
        return json.dumps(
            {"status": "error", "message": f"Terminal {terminal_id!r} não encontrado."}
        )
    return json.dumps({"status": "closed", "terminal_id": terminal_id})


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="filesystem",
        destructive=False,
        icon="terminal",
    )
)
async def read_terminal(
    terminal_id: str,
    cursor: int | None = None,
    max_bytes: int = 8192,
    ctx: ToolContext = _DEFAULT_CONTEXT,
) -> str:
    """Lê incrementalmente a saída retida de um terminal desta sessão."""
    if not terminal_id:
        return json.dumps({"status": "error", "code": "terminal_required"})
    session = pty_registry.resolve_for_context(
        terminal_id,
        user_id=ctx.user_id,
        thread_id=ctx.thread_id,
        workspace_id=ctx.workspace_id,
    )
    if session is None:
        code = (
            "closed"
            if pty_registry.was_closed_for_context(
                terminal_id,
                user_id=ctx.user_id,
                thread_id=ctx.thread_id,
                workspace_id=ctx.workspace_id,
            )
            else "not_found"
        )
        return json.dumps({"status": "error", "code": code})
    retry_after = session.consume_rate_limit()
    if retry_after is not None:
        return json.dumps(
            {"status": "error", "code": "rate_limited", "retry_after": retry_after}
        )
    try:
        result = session.read_since(cursor, max_bytes)
    except (TypeError, ValueError):
        return json.dumps({"status": "error", "code": "invalid_cursor"})
    data = result.pop("data").decode("utf-8", errors="replace")
    from backend.services.git import redact_git_output

    result.update({"status": "ok", "output": redact_git_output(data)})
    return json.dumps(result, ensure_ascii=False)


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="filesystem",
        destructive=True,
        icon="terminal",
    )
)
async def write_terminal(
    terminal_id: str,
    input_data: str,
    request_id: str = "",
    ctx: ToolContext = _DEFAULT_CONTEXT,
) -> str:
    """Escreve entrada explícita no terminal após aprovação HITL."""
    if not terminal_id or not input_data:
        return json.dumps({"status": "error", "code": "input_required"})
    if len(input_data.encode("utf-8")) > 8192:
        return json.dumps({"status": "error", "code": "input_too_large"})
    if not request_id:
        return json.dumps({"status": "error", "code": "request_id_required"})
    session = pty_registry.resolve_for_context(
        terminal_id,
        user_id=ctx.user_id,
        thread_id=ctx.thread_id,
        workspace_id=ctx.workspace_id,
    )
    if session is None:
        code = (
            "closed"
            if pty_registry.was_closed_for_context(
                terminal_id,
                user_id=ctx.user_id,
                thread_id=ctx.thread_id,
                workspace_id=ctx.workspace_id,
            )
            else "not_found"
        )
        return json.dumps({"status": "error", "code": code})
    retry_after = session.consume_rate_limit()
    if retry_after is not None:
        return json.dumps(
            {"status": "error", "code": "rate_limited", "retry_after": retry_after}
        )
    return json.dumps(
        session.write_input(input_data.encode("utf-8"), request_id), ensure_ascii=False
    )


__all__ = [
    "attach_terminal",
    "close_terminal",
    "list_terminals",
    "open_terminal",
    "read_terminal",
    "write_terminal",
]
