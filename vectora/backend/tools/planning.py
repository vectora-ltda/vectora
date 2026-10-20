"""Tool nativa de planejamento — ``write_todos``.

Registrada como qualquer outra ``@vtool`` e exposta a todos os agentes
via ``ALL_TOOLS``/``CHAT_TOOLS`` (``backend/nodes/tools.py``).

O agente usa ``write_todos`` pra quebrar uma tarefa complexa em passos e
manter o usuário informado do progresso em tempo real: cada chamada
substitui a lista inteira (não é incremental), e ``backend/engine/
conversation_loop.py`` traduz o resultado em ``TodosUpdated``, consumido
pela aba Plan do workbench no frontend.
"""

from __future__ import annotations

import json
from typing import Literal

from pydantic import BaseModel

from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool


class TodoItemArg(BaseModel):
    """Um item da checklist — mesmo shape de ``backend.engine.stream_events.TodoItem``."""

    content: str
    status: Literal["pending", "in_progress", "completed"]
    step_id: str | None = None
    quality_gate: str | None = None


@vtool(extras=ToolExtras(category="planning", icon="list-checks"))
async def write_todos(todos: list[TodoItemArg], ctx: ToolContext) -> str:
    """Substitui a lista de tarefas inteira (não incremental) — use pra
    quebrar uma tarefa complexa em passos e manter o usuário informado do
    progresso em tempo real, na aba Plan do workbench.

    Args:
        todos: lista completa e atualizada de tarefas, cada uma com
            content (descrição curta) e status (pending/in_progress/completed).
    """
    payload = [
        {
            "content": t.content,
            "status": t.status,
            **({"step_id": t.step_id} if t.step_id is not None else {}),
            **({"quality_gate": t.quality_gate} if t.quality_gate is not None else {}),
        }
        for t in todos
    ]
    return json.dumps(payload, ensure_ascii=False)
