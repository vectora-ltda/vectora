"""``execute_tool_batch`` — paralelização de tool calls do mesmo turno.
Substitui ``backend/nodes/parallel_tools.py::ParallelToolNode(ToolNode)``.

Regra de segurança de paralelismo: tool calls não-destrutivas do mesmo lote
rodam via ``asyncio.gather`` (mesma granularidade que ``ToolExtras.
destructive`` já expõe); qualquer tool destrutiva no lote força execução
sequencial do lote inteiro — evita duas escritas concorrentes na mesma
sessão/arquivo/recurso externo por causa de paralelismo, mesmo quando só
uma das chamadas é a arriscada.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from backend.vtypes.message import ContentBlock, MessageRole, VMessage

if TYPE_CHECKING:
    from backend.engine.guardrails import TurnBudget
    from backend.tools.context import ToolContext
    from backend.tools.registry import ToolRegistry
    from backend.vtypes.message import ToolCall

#: Padrões de segredo comuns que podem vazar no stdout de `terminal` ou no
#: conteúdo de `file_read` (variável de ambiente ecoada, chave colada num
#: log lido) — cada match vira "[REDACTED]" antes do resultado ser
#: persistido no histórico ou mostrado ao LLM.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),  # OpenAI/Anthropic/etc-style keys
    re.compile(r"ghp_[A-Za-z0-9]{36}"),  # GitHub personal access tokens
    re.compile(r"AKIA[0-9A-Z]{16}"),  # AWS access key IDs
)


def _redact_secrets(texto: str) -> str:
    for pattern in _SECRET_PATTERNS:
        texto = pattern.sub("[REDACTED]", texto)
    return texto


#: Callbacks aplicados, em ordem, ao texto de resultado de toda tool antes
#: de persistir/emitir. Não é um sistema de plugin genérico — não há
#: registro dinâmico vindo de fora deste módulo, é uma lista fixa e
#: revisada, editada só aqui (CLAUDE.md: features nativas, não extensíveis
#: por terceiros).
_POST_EXECUTE: tuple[Callable[[str], str], ...] = (_redact_secrets,)


def _apply_post_execute(texto: str) -> str:
    for hook in _POST_EXECUTE:
        texto = hook(texto)
    return texto


async def _run_one(
    tool_call: ToolCall,
    *,
    tool_registry: ToolRegistry,
    ctx: ToolContext,
    turn_budget: TurnBudget | None,
) -> VMessage:
    """Executa uma chamada do lote com contexto correlacionado à própria tool."""
    spec = tool_registry.get(tool_call.name)

    if turn_budget is not None:
        estourado = turn_budget.record_tool_call(spec)
        if estourado is not None:
            return VMessage(
                role=MessageRole.TOOL,
                content=[
                    ContentBlock(
                        kind="text",
                        text=(
                            f"Error: teto de guardrail do turno excedido "
                            f"({estourado}) — tool '{tool_call.name}' não executada."
                        ),
                    )
                ],
                tool_call_id=tool_call.id,
                name=tool_call.name,
                is_error=True,
            )

    if spec is None:
        texto = f"Error: tool '{tool_call.name}' não encontrada no registry"
        is_error = True
    else:
        # O contexto é por chamada para que uma delegação paralela mantenha
        # sua correlação própria sem sobrescrever a de outra tool.
        texto = await spec.ainvoke(
            tool_call.args, replace(ctx, tool_call_id=tool_call.id)
        )
        texto = _apply_post_execute(texto)
        is_error = texto.startswith("Error:")
    return VMessage(
        role=MessageRole.TOOL,
        content=[ContentBlock(kind="text", text=texto)],
        tool_call_id=tool_call.id,
        name=tool_call.name,
        is_error=is_error,
    )


async def execute_tool_batch(
    tool_calls: list[ToolCall],
    *,
    tool_registry: ToolRegistry,
    ctx: ToolContext,
    turn_budget: TurnBudget | None = None,
) -> list[VMessage]:
    """Executa todas as `tool_calls` do turno, na ordem em que aparecem no
    resultado — paralelo se nenhuma é destrutiva, sequencial (mas ainda
    assim todas executadas) se qualquer uma é.

    Com `turn_budget`, cada chamada é registrada contra o teto do turno
    (`backend/engine/guardrails.py::TurnBudget`) antes de rodar — a
    primeira chamada que estourar o teto vira erro sem executar a tool, e
    `turn_budget.exceeded` fica setado (travado) pro chamador checar depois
    do lote inteiro e decidir parar o loop."""
    algum_destrutivo = any(
        (spec := tool_registry.get(tc.name)) is not None and spec.extras.destructive
        for tc in tool_calls
    )

    if algum_destrutivo:
        return [
            await _run_one(
                tc, tool_registry=tool_registry, ctx=ctx, turn_budget=turn_budget
            )
            for tc in tool_calls
        ]

    return list(
        await asyncio.gather(
            *(
                _run_one(
                    tc, tool_registry=tool_registry, ctx=ctx, turn_budget=turn_budget
                )
                for tc in tool_calls
            )
        )
    )
