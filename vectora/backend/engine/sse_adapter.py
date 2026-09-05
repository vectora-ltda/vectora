"""``to_sse_line`` — serializa um ``EngineEvent`` nativo (``stream_events.py``)
pra uma linha SSE, usando os schemas Pydantic existentes em ``backend/api/
schemas.py`` (``ThreadEvent``, ``TokenEvent``, etc.) e o `encode_event()`
que já serializa esse envelope — o shape que chega ao frontend
(``data: {"type": ..., ...}\\n\\n``) é idêntico, byte a byte por campo.

Mapeamento 1:1 — cada `EngineEvent` vira exatamente o schema Pydantic
`_TYPE_MAP` já associa ao mesmo `type` de hoje (`backend/api/schemas.py`).
"""

from __future__ import annotations

from backend.api import schemas
from backend.engine.stream_events import (
    Done,
    EngineEvent,
    ErrorSignal,
    HitlRequested,
    MessageBreak,
    MessageChunk,
    ModelSwitched,
    NodeStatus,
    RagCitations,
    SubagentOutput,
    TerminalLine,
    ThreadStarted,
    TodosUpdated,
    ToolActivity,
    ToolCallStarted,
    ToolResult,
    UIMetrics,
    WorkbenchInvalidate,
)


def _to_payload(event: EngineEvent) -> schemas.StreamChatEventPayload:  # noqa: PLR0911
    """Converte o `EngineEvent` nativo pro schema Pydantic correspondente —
    dispatch por tipo exato (não por herança: os dataclasses de evento não
    compartilham base além do Union)."""
    if isinstance(event, ThreadStarted):
        return schemas.ThreadEvent(
            thread_id=event.thread_id, workspace_id=event.workspace_id
        )
    if isinstance(event, MessageChunk):
        return schemas.TokenEvent(content=event.content, node=event.node)
    if isinstance(event, MessageBreak):
        return schemas.MessageBreakEvent()
    if isinstance(event, ToolCallStarted):
        return schemas.ToolCallEvent(
            tool_name=event.tool_name,
            tool_call_id=event.tool_call_id,
            args_json=event.args_json,
            render_hint=event.render_hint,
            category=event.category,
            destructive=event.destructive,
            icon=event.icon,
        )
    if isinstance(event, ToolResult):
        return schemas.ToolResultEvent(
            tool_call_id=event.tool_call_id,
            content_json=event.content_json,
            is_error=event.is_error,
        )
    if isinstance(event, ToolActivity):
        return schemas.ToolActivityEvent(
            tool_name=event.tool_name,
            tool_call_id=event.tool_call_id,
            args_preview=event.args_preview,
            elapsed_ms=event.elapsed_ms,
        )
    if isinstance(event, TerminalLine):
        return schemas.TerminalLineEvent(line=event.line)
    if isinstance(event, SubagentOutput):
        return schemas.SubagentOutputEvent(
            subagent_type=event.subagent_type,
            description=event.description,
            status=event.status,
            tool_call_id=event.tool_call_id,
            content=event.content,
            is_delta=event.is_delta,
        )
    if isinstance(event, NodeStatus):
        return schemas.NodeEvent(
            node=event.node,
            status=event.status,
            duration_ms=event.duration_ms,
            node_label=event.node_label,
        )
    if isinstance(event, RagCitations):
        return schemas.RagCitationEvent(
            citations=[
                schemas.RagCitation(index=c.index, source=c.source, chunk=c.chunk)
                for c in event.citations
            ]
        )
    if isinstance(event, HitlRequested):
        return schemas.HITLEvent(
            tool_name=event.tool_name,
            args_json=event.args_json,
            interrupt_id=event.interrupt_id,
            reasoning=event.reasoning,
            affected_paths=event.affected_paths,
            diff_preview=event.diff_preview,
            pre_approved=event.pre_approved,
        )
    if isinstance(event, WorkbenchInvalidate):
        return schemas.WorkbenchInvalidateEvent(
            tabs=event.tabs, tool_name=event.tool_name
        )
    if isinstance(event, TodosUpdated):
        return schemas.TodosUpdatedEvent(
            todos=[
                schemas.TodoItem(content=t.content, status=t.status)
                for t in event.todos
            ]
        )
    if isinstance(event, ModelSwitched):
        return schemas.ModelSwitchedEvent(
            from_model=event.from_model, to_model=event.to_model
        )
    if isinstance(event, UIMetrics):
        return schemas.UIMetricsEvent(
            last_node=event.last_node,
            last_node_ms=event.last_node_ms,
            rag_hits=event.rag_hits,
            rag_misses=event.rag_misses,
            tool_calls=event.tool_calls,
        )
    if isinstance(event, ErrorSignal):
        return schemas.ErrorEvent(message=event.message, code=event.code)
    if isinstance(event, Done):
        return schemas.DoneEvent(thread_id=event.thread_id, run_id=event.run_id)

    msg = f"EngineEvent sem mapeamento SSE: {type(event).__name__}"
    raise TypeError(msg)


def to_sse_line(event: EngineEvent) -> str:
    """`data: {"type": ..., ...}\\n\\n` — mesmo formato que o frontend já
    consome hoje via `use-stream-handler.ts`."""
    return schemas.encode_event(_to_payload(event))
