"""Sequential Thinking Tool.

Implementa a spec MCP Anthropic de sequential thinking: permite ao agente
raciocinar passo a passo, revisitar pensamentos anteriores e bifurcar o
raciocínio em alternativas antes de agir.

Cada invocação registra um passo de pensamento e retorna o estado atual,
permitindo ao agente construir cadeias de raciocínio auditáveis.
"""

from __future__ import annotations

import json
import logging

from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)


@vtool(
    extras=ToolExtras(
        render_hint="thinking_step",
        category="reasoning",
        destructive=False,
        icon="brain",
    )
)
async def sequential_thinking(
    thought: str,
    thought_number: int,
    total_thoughts: int,
    is_revision: bool = False,
    revises_thought: int | None = None,
    branch_from_thought: int | None = None,
    branch_id: str | None = None,
    next_thought_needed: bool = True,
) -> str:
    """Registra um passo de raciocínio sequencial antes de agir.

    Use para pensar em voz alta, revisitar conclusões anteriores e explorar
    alternativas antes de tomar uma ação final. O frontend renderiza cada
    passo como um accordion colapsável.

    Args:
        thought: O conteúdo do pensamento atual
        thought_number: Número sequencial deste pensamento (começa em 1)
        total_thoughts: Total estimado de pensamentos para completar a análise
        is_revision: True se este pensamento revisa um pensamento anterior
        revises_thought: Número do pensamento sendo revisado (se is_revision=True)
        branch_from_thought: Bifurca a partir deste número de pensamento
        branch_id: Identificador único para este ramo de raciocínio
        next_thought_needed: False quando o raciocínio está completo
    """
    try:
        if thought_number > total_thoughts:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"thought_number ({thought_number}) não pode exceder total_thoughts ({total_thoughts})",
                }
            )

        is_final = (thought_number == total_thoughts and not next_thought_needed) or (
            thought_number == total_thoughts
        )

        result: dict = {
            "thought": thought,
            "thought_number": thought_number,
            "total_thoughts": total_thoughts,
            "is_final": is_final,
            "next_thought_needed": next_thought_needed and not is_final,
        }

        if is_revision:
            result["is_revision"] = True
            if revises_thought is not None:
                result["revises_thought"] = revises_thought

        if branch_from_thought is not None:
            result["branch_from_thought"] = branch_from_thought
        if branch_id is not None:
            result["branch_id"] = branch_id

        logger.debug(
            "sequential_thinking: step=%d/%d final=%s revision=%s",
            thought_number,
            total_thoughts,
            is_final,
            is_revision,
        )
        return json.dumps(result)

    except Exception as e:
        logger.exception("sequential_thinking: erro inesperado")
        return json.dumps({"status": "error", "error": str(e)})
