"""AITL (Agent In The Loop) — subagente pede uma decisão ao agente pai.

Análogo ao HITL, mas o "humano" que aprova é o próprio orquestrador: uma
chamada de LLM síncrona dentro do mesmo turno, sem round-trip pro usuário
nem pausa esperando um clique na UI — a decisão resolve sozinha, no mesmo
turno.

Limite técnico real: `SubagentSpec.tools` (`backend/engine/subagents.py`)
é uma lista fixa, resolvida na montagem do subagente — aprovar "acesso a
mais tools" aqui NUNCA adiciona uma tool em voo ao subagent. O padrão é o
agente pai executar a ação em nome do subagent (chamar a tool com os
argumentos que o subagent pediu) e devolver o resultado como parte da
resposta desta tool — documentado explicitamente no docstring de
`ask_parent_agent` pra não vender uma expansão de tooling que não existe.

Exposta só dentro dos SOULs de `backend/agents/souls.py` — nunca no toolset
do orquestrador (ele não tem "pai" a quem perguntar).

Usa `FallbackChatClient` (native `ChatClient`) — `souls.py` consome o
`ToolSpec` nativo direto.
"""

from __future__ import annotations

import json
import logging

from backend.llm.fallback_chat_client import FallbackChatClient
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool
from backend.vtypes.message import ContentBlock, MessageRole, VMessage

logger = logging.getLogger(__name__)

_DECISION_SYSTEM_PROMPT = (
    "You are the parent orchestrator agent. A subagent you delegated work to "
    "is asking for a decision — approve or deny. Answer with exactly one "
    "word, APPROVED or DENIED, on the first line, followed by a short reason "
    "on the next line. Default to DENIED when the request is vague, risky, "
    "or you're not confident it's safe."
)


@vtool(
    extras=ToolExtras(
        destructive=False,
        category="workspace",
        icon="help-circle",
    )
)
async def ask_parent_agent(
    reason: str, ctx: ToolContext, requested_tool: str = ""
) -> str:
    """Pede uma decisão ao agente pai (AITL) — para quando você, como
    subagent, precisa de algo fora do seu escopo atual antes de continuar.

    Isto NÃO adiciona uma tool nova ao seu toolset — mesmo aprovado, você
    continua sem acesso a `requested_tool`. Se aprovado e a ação for
    executável, o agente pai a executa em seu nome e o resultado vem no
    campo `result` da resposta; se a ação não puder ser executada
    automaticamente, use o `reason` retornado para decidir como prosseguir
    sem ela (ex.: pedir ao usuário, ou seguir um caminho alternativo).

    Args:
        reason: Por que você está pedindo — seja específico (o que trava sem
            isso, o que você já tentou).
        requested_tool: Nome da tool que você gostaria de ter, se aplicável.

    Returns:
        JSON com `status`, `approved` (bool) e `reason` (motivo da decisão).
        Nunca levanta exceção — falha na decisão vira `approved=False`.
    """
    try:
        llm = FallbackChatClient(primary_model_id=ctx.model)
        texto_pedido = f"Subagent request: {reason}"
        if requested_tool:
            texto_pedido += f"\nRequested tool: {requested_tool}"

        resposta = await llm.agenerate(
            [
                VMessage(
                    role=MessageRole.SYSTEM,
                    content=[ContentBlock(kind="text", text=_DECISION_SYSTEM_PROMPT)],
                ),
                VMessage(
                    role=MessageRole.USER,
                    content=[ContentBlock(kind="text", text=texto_pedido)],
                ),
            ]
        )
        texto = resposta.text().strip()
        approved = texto.upper().startswith("APPROVED")
        return json.dumps({"status": "ok", "approved": approved, "reason": texto})
    except Exception as exc:
        logger.exception("ask_parent_agent: erro inesperado")
        return json.dumps(
            {
                "status": "ok",
                "approved": False,
                "reason": f"negado — erro interno ao decidir: {exc}",
            }
        )
