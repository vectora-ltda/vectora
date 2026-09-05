"""Vocabulário de eventos do motor nativo — análogo a ``gateway/
stream_events.py`` do Hermes Agent: um dataclass
``frozen=True`` por tipo de evento SSE hoje existente (17 tipos, campos
idênticos aos payloads que ``backend/api/schemas.py`` já define e que o
frontend já consome via ``use-stream-handler.ts``).

Por que dataclasses novos em vez de reusar os schemas Pydantic direto: o
motor nativo (``backend/engine/*``) não depende de Pydantic pra estado
interno — só na borda, ao serializar pra SSE (``sse_adapter.py``), é que
cada evento vira o schema Pydantic correspondente. Mesma separação que
``VMessage``/``VMessageChunk`` já seguem em relação aos schemas de API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol


@dataclass(frozen=True, slots=True)
class ThreadStarted:
    thread_id: str
    workspace_id: str = ""


@dataclass(frozen=True, slots=True)
class MessageChunk:
    content: str
    node: str = ""


@dataclass(frozen=True, slots=True)
class MessageBreak:
    """Sinaliza quebra de bolha — sem payload, mesmo shape vazio de
    `MessageBreakEvent`."""


@dataclass(frozen=True, slots=True)
class ToolCallStarted:
    tool_name: str
    tool_call_id: str
    args_json: str
    render_hint: str = "json"
    category: str = "general"
    destructive: bool = False
    icon: str = "tool"


@dataclass(frozen=True, slots=True)
class ToolResult:
    tool_call_id: str
    content_json: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class ToolActivity:
    tool_name: str
    tool_call_id: str = ""
    args_preview: str = ""
    elapsed_ms: int | None = None


@dataclass(frozen=True, slots=True)
class TerminalLine:
    line: str


@dataclass(frozen=True, slots=True)
class SubagentOutput:
    """Atualização do card de uma delegação interna.

    Quando ``is_delta`` é verdadeiro, ``content`` é um trecho incremental do
    texto produzido pelo subagent; caso contrário, é o resultado acumulado ou
    final da delegação.
    """

    subagent_type: str
    description: str = ""
    status: str = "running"
    tool_call_id: str = ""
    content: str = ""
    is_delta: bool = False


@dataclass(frozen=True, slots=True)
class NodeStatus:
    node: str
    status: Literal["started", "finished"]
    duration_ms: int = 0
    node_label: str = ""


@dataclass(frozen=True, slots=True)
class RagCitation:
    index: int
    source: str
    chunk: str = ""


@dataclass(frozen=True, slots=True)
class RagCitations:
    citations: list[RagCitation]


@dataclass(frozen=True, slots=True)
class HitlRequested:
    tool_name: str
    args_json: str
    interrupt_id: str
    reasoning: str = ""
    affected_paths: list[str] = field(default_factory=list)
    diff_preview: str = ""
    pre_approved: bool = False


@dataclass(frozen=True, slots=True)
class WorkbenchInvalidate:
    tabs: list[str]
    tool_name: str = ""


@dataclass(frozen=True, slots=True)
class TodoItem:
    content: str
    status: Literal["pending", "in_progress", "completed"]


@dataclass(frozen=True, slots=True)
class TodosUpdated:
    todos: list[TodoItem]


@dataclass(frozen=True, slots=True)
class ModelSwitched:
    from_model: str
    to_model: str


@dataclass(frozen=True, slots=True)
class UIMetrics:
    last_node: str = ""
    last_node_ms: int = 0
    rag_hits: int = 0
    rag_misses: int = 0
    tool_calls: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ErrorSignal:
    message: str
    code: str = "INTERNAL"


@dataclass(frozen=True, slots=True)
class Done:
    thread_id: str
    run_id: str = ""


EngineEvent = (
    ThreadStarted
    | MessageChunk
    | MessageBreak
    | ToolCallStarted
    | ToolResult
    | ToolActivity
    | TerminalLine
    | SubagentOutput
    | NodeStatus
    | RagCitations
    | HitlRequested
    | WorkbenchInvalidate
    | TodosUpdated
    | ModelSwitched
    | UIMetrics
    | ErrorSignal
    | Done
)


class EventSink(Protocol):
    """Callback que o loop de conversa e demais produtores do motor nativo
    chamam pra emitir um `EngineEvent` — o adapter (`sse_adapter.py`) é só
    um dos consumidores possíveis dessa interface."""

    async def __call__(self, event: EngineEvent) -> None: ...
