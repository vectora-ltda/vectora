"""Executa a revisão de PR self-hosted (mensagem `review_job` recebida pelo
túnel do gateway, ver `backend/services/gateway/__init__.py::_dispatch`) —
mesmo caminho de motor nativo que `backend/cli/run_task.py` usa no modo
one-shot (CI), mas SEM fechar o `agent_factory` no final: esta função roda
DENTRO do servidor real já em execução, com o `agent_factory` compartilhado
pelo resto do app — fechá-lo aqui derrubaria o chat normal do usuário.
"""

from __future__ import annotations

import logging
import os
from uuid import uuid4

logger = logging.getLogger(__name__)

#: Mesmo valor/motivo de `run_task.py::_PERMISSION_MODE` — um review job
#: assíncrono não tem UI de aprovar/rejeitar tool call.
_PERMISSION_MODE = "auto"


class ReviewJobModelNotConfiguredError(Exception):
    """Levantada quando VECTORA_MODEL não está setado nesta instância —
    revisão self-hosted usa o modelo padrão da própria instância do
    usuário (mesma convenção de `vectora run`), não o provider/modelo
    configurado no painel do gh-bot (esse é só o fallback do modo hosted)."""


def _build_task(diff: str, metadata: dict[str, str]) -> str:
    contexto = "\n".join(f"- {k}: {v}" for k, v in metadata.items())
    return (
        "Revise o diff de um Pull Request e escreva um comentário de "
        "review objetivo (bugs reais, riscos, sugestões — sem elogio "
        "genérico).\n\n"
        f"Contexto:\n{contexto}\n\nDiff:\n{diff}"
    )


async def run_review_job(diff: str, metadata: dict[str, str]) -> str:
    """Roda a revisão e devolve o texto final — levanta em caso de erro
    (chamador decide como reportar, ver `GatewayClient._handle_review_job`)."""
    from backend.engine.conversation_loop import LoopConfig, run_conversation
    from backend.engine.hitl import should_require_approval
    from backend.llm.fallback_chat_client import FallbackChatClient
    from backend.services import agent_factory
    from backend.tools.registry import ToolRegistry
    from backend.vtypes.context import ctx_from_config
    from backend.vtypes.message import MessageRole, text_message

    model_id = os.environ.get("VECTORA_MODEL", "")
    if not model_id:
        raise ReviewJobModelNotConfiguredError(
            "VECTORA_MODEL não configurado nesta instância — defina a env "
            "var (ex.: google_genai:gemini-3-flash) pra habilitar revisão "
            "self-hosted."
        )

    thread_id = f"gha-review-{uuid4().hex}"
    task = _build_task(diff, metadata)

    native_agent = await agent_factory.get_native_agent(
        user_id="local", chat_mode=False, workspace_id=None
    )
    session_store = await agent_factory.get_session_store()
    approval_gate = await agent_factory.get_approval_gate()

    await session_store.create_session(
        thread_id,
        user_id="local",
        mode="gha-review",
        permission_mode=_PERMISSION_MODE,
    )
    parent_id = await session_store.append_message(
        thread_id,
        text_message(MessageRole.SYSTEM, native_agent.system_prompt),
    )
    await session_store.append_message(
        thread_id,
        text_message(MessageRole.USER, task),
        parent_message_id=parent_id,
    )

    configurable = {
        "thread_id": thread_id,
        "user_id": "local",
        "permission_mode": _PERMISSION_MODE,
    }
    run_ctx = ctx_from_config({"configurable": configurable})
    run_ctx.store = await agent_factory.get_store()
    chat_client = FallbackChatClient(primary_model_id=model_id)
    loop_config = LoopConfig(max_iterations=50)

    # `diff` vem de um PR de terceiros — conteúdo não-confiável. Rodar com
    # `native_agent.tool_registry` (fs/git/web/mcp, todas as ferramentas do
    # agente completo) e `permission_mode="auto"` daria a uma instrução
    # maliciosa embutida no diff acesso irrestrito a filesystem, rede e
    # delegação de subagents, sem nenhum humano no loop pra aprovar. Um
    # registry vazio elimina a superfície inteira: o modelo só pode
    # responder texto (a revisão), nunca invocar uma tool.
    result = await run_conversation(
        session_store=session_store,
        chat_client=chat_client,
        tool_registry=ToolRegistry(),
        ctx=run_ctx,
        thread_id=thread_id,
        config=loop_config,
        should_require_approval=should_require_approval,
        approval_gate=approval_gate,
    )

    return result.final_message.text() if result.final_message else ""
