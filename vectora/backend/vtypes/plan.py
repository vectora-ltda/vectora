"""Contratos tipados do Kanban e do planejamento de tarefas."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

KanbanStatus = Literal[
    "triage",
    "todo",
    "scheduled",
    "ready",
    "running",
    "blocked",
    "review",
    "done",
    "archived",
]
BlockKind = Literal["dependency", "needs_input", "capability", "transient"]


class DecompositionNode(TypedDict):
    """Nó do grafo de subtarefas produzido pela ferramenta de decomposição."""

    name: str
    instruction: str
    parents: list[int]


class KanbanCard(TypedDict):
    """Representação estável de um card entregue ao agente e à UI."""

    task_id: str
    name: str
    kind: str
    status: KanbanStatus
    block_kind: NotRequired[BlockKind | None]
    block_reason: NotRequired[str | None]
    agent_profile_id: NotRequired[str | None]
    last_run_at: NotRequired[str | None]
