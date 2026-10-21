"""Execução do agente para mensagens vindas de plataformas externas.

Um único ponto de entrada para os 4 adapters: eles só traduzem formato, quem
resolve thread e roda o agente é aqui — assim uma mensagem do Telegram passa
exatamente pelo mesmo caminho de uma do chat web (motor nativo,
``backend/engine/conversation_loop.py``).
"""

from __future__ import annotations

import logging

from backend.engine.conversation_loop import LoopConfig, run_conversation
from backend.engine.hitl import should_require_approval
from backend.llm.fallback_chat_client import FallbackChatClient
from backend.services.connect.store import create_thread_mapping, lookup_thread
from backend.services.gateway.messaging import (
    IncomingMessage,
    OutgoingMessage,
    handle_incoming_message,
)
from backend.vtypes.context import ctx_from_config
from backend.vtypes.message import MessageRole, text_message

logger = logging.getLogger(__name__)

#: Teto de caracteres da resposta devolvida à plataforma. Telegram corta em
#: 4096 e Discord em 2000; truncar aqui evita que a plataforma rejeite a
#: mensagem inteira e o interlocutor fique sem resposta nenhuma.
MAX_REPLY_CHARS = 1900

#: Mensageria externa opera sob escopo seguro e restrito: interlocutores
#: externos (Telegram/Discord/Slack/Email) não possuem interface interativa
#: de aprovação (HITL). Ferramentas destrutivas são desabilitadas e o conteúdo
#: é classificado como untrusted_content via política compilada.
_EXTERNAL_PERMISSION_MODE = "auto"


async def run_agent_for_thread(thread_id: str, text: str) -> str:
    """Roda o agente do usuário local no `thread_id` dado e devolve o texto."""
    from backend.services import agent_factory

    native_agent = await agent_factory.get_native_agent(
        user_id="local", chat_mode=False, workspace_id=None
    )
    session_store = await agent_factory.get_session_store()
    approval_gate = await agent_factory.get_approval_gate()

    await session_store.create_session(
        thread_id,
        user_id="local",
        mode="connect",
        permission_mode=_EXTERNAL_PERMISSION_MODE,
    )
    parent_id = await session_store.get_branch_head_id(thread_id)
    if parent_id is None:
        parent_id = await session_store.append_message(
            thread_id, text_message(MessageRole.SYSTEM, native_agent.system_prompt)
        )
    await session_store.append_message(
        thread_id,
        text_message(MessageRole.USER, text),
        parent_message_id=parent_id,
    )

    configurable = {
        "thread_id": thread_id,
        "user_id": "local",
        "permission_mode": _EXTERNAL_PERMISSION_MODE,
    }
    run_ctx = ctx_from_config({"configurable": configurable})
    run_ctx._extra["untrusted_content"] = True
    run_ctx.store = await agent_factory.get_store()
    chat_client = FallbackChatClient(primary_model_id="")
    loop_config = LoopConfig(max_iterations=50)

    from backend.services.policy_compiler import compile_policy_snapshot

    snapshot = compile_policy_snapshot(run_ctx, untrusted_content=True)

    if native_agent.subagent_catalog:
        from backend.tools.subagent_delegate import SubagentDeps

        run_ctx._extra["subagent_deps"] = SubagentDeps(
            catalog=native_agent.subagent_catalog,
            session_store=session_store,
            chat_client=chat_client,
            config=loop_config,
            should_require_approval=should_require_approval,
            policy_snapshot=snapshot,
        )

    result = await run_conversation(
        session_store=session_store,
        chat_client=chat_client,
        tool_registry=native_agent.tool_registry,
        ctx=run_ctx,
        thread_id=thread_id,
        config=loop_config,
        should_require_approval=should_require_approval,
        approval_gate=approval_gate,
        policy_snapshot=snapshot,
    )

    reply = result.final_message.text() if result.final_message else ""
    if len(reply) > MAX_REPLY_CHARS:
        reply = reply[:MAX_REPLY_CHARS] + "…"
    return reply


async def process_incoming(incoming: IncomingMessage) -> OutgoingMessage:
    """Resolve o thread e roda o agente. Nunca levanta: `handle_incoming_message`
    já converte falha em texto de erro amigável, senão o interlocutor externo
    ficaria sem resposta nenhuma."""
    return await handle_incoming_message(
        incoming,
        lookup=lookup_thread,
        create=create_thread_mapping,
        run_agent=run_agent_for_thread,
    )
