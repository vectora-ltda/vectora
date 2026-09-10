"""``to_sse_line`` — serialização do vocabulário nativo de eventos pro
mesmo contrato SSE que o frontend já consome hoje via
`use-stream-handler.ts`. Paridade byte-a-byte: cada `EngineEvent` precisa
produzir a MESMA linha que `backend/api/schemas.py::encode_event` já produz
pro schema Pydantic equivalente — não uma reimplementação paralela.
"""

from __future__ import annotations

import json

import pytest

from backend.api import schemas
from backend.engine.sse_adapter import to_sse_line
from backend.engine.stream_events import (
    Done,
    ErrorSignal,
    HitlRequested,
    MessageBreak,
    MessageChunk,
    ModelSwitched,
    NodeStatus,
    RagCitation,
    RagCitations,
    StructuredQuestionRequested,
    SubagentOutput,
    TerminalLine,
    ThreadStarted,
    TodoItem,
    TodosUpdated,
    ToolActivity,
    ToolCallStarted,
    ToolResult,
    UIMetrics,
    WorkbenchInvalidate,
)


def _parse(linha: str) -> dict:
    assert linha.startswith("data: ")
    assert linha.endswith("\n\n")
    return json.loads(linha[len("data: ") : -2])


class TestParidadeComOSchemaPydantic:
    """Cada evento nativo precisa bater exatamente com o schema Pydantic
    que já é fonte de verdade do contrato SSE hoje."""

    def test_thread_started(self):
        nativo = to_sse_line(ThreadStarted(thread_id="t1", workspace_id="w1"))
        pydantic = schemas.encode_event(
            schemas.ThreadEvent(thread_id="t1", workspace_id="w1")
        )
        assert nativo == pydantic
        assert _parse(nativo)["type"] == "thread"

    def test_message_chunk(self):
        nativo = to_sse_line(MessageChunk(content="oi", node="agent"))
        pydantic = schemas.encode_event(schemas.TokenEvent(content="oi", node="agent"))
        assert nativo == pydantic
        assert _parse(nativo)["type"] == "token"

    def test_message_break(self):
        nativo = to_sse_line(MessageBreak())
        pydantic = schemas.encode_event(schemas.MessageBreakEvent())
        assert nativo == pydantic
        assert _parse(nativo)["type"] == "message_break"

    def test_tool_call_started(self):
        nativo = to_sse_line(
            ToolCallStarted(
                tool_name="file_read",
                tool_call_id="call_1",
                args_json='{"path":"a.py"}',
                destructive=False,
            )
        )
        pydantic = schemas.encode_event(
            schemas.ToolCallEvent(
                tool_name="file_read",
                tool_call_id="call_1",
                args_json='{"path":"a.py"}',
                destructive=False,
            )
        )
        assert nativo == pydantic
        assert _parse(nativo)["type"] == "tool_call"

    def test_tool_result(self):
        nativo = to_sse_line(
            ToolResult(tool_call_id="call_1", content_json="ok", is_error=False)
        )
        pydantic = schemas.encode_event(
            schemas.ToolResultEvent(
                tool_call_id="call_1", content_json="ok", is_error=False
            )
        )
        assert nativo == pydantic
        assert _parse(nativo)["type"] == "tool_result"

    def test_tool_activity(self):
        nativo = to_sse_line(
            ToolActivity(tool_name="terminal", tool_call_id="call_1", elapsed_ms=42)
        )
        pydantic = schemas.encode_event(
            schemas.ToolActivityEvent(
                tool_name="terminal", tool_call_id="call_1", elapsed_ms=42
            )
        )
        assert nativo == pydantic
        assert _parse(nativo)["type"] == "tool_activity"

    def test_terminal_line(self):
        nativo = to_sse_line(TerminalLine(line="$ ls"))
        pydantic = schemas.encode_event(schemas.TerminalLineEvent(line="$ ls"))
        assert nativo == pydantic
        assert _parse(nativo)["type"] == "terminal_line"

    def test_subagent_output(self):
        nativo = to_sse_line(
            SubagentOutput(subagent_type="coder", status="complete", content="feito")
        )
        pydantic = schemas.encode_event(
            schemas.SubagentOutputEvent(
                subagent_type="coder", status="complete", content="feito"
            )
        )
        assert nativo == pydantic
        assert _parse(nativo)["type"] == "subagent_output"

    def test_node_status(self):
        nativo = to_sse_line(
            NodeStatus(node="agent", status="finished", duration_ms=120)
        )
        pydantic = schemas.encode_event(
            schemas.NodeEvent(node="agent", status="finished", duration_ms=120)
        )
        assert nativo == pydantic
        assert _parse(nativo)["type"] == "node"

    def test_rag_citations(self):
        nativo = to_sse_line(
            RagCitations(
                citations=[RagCitation(index=1, source="doc.md", chunk="trecho")]
            )
        )
        pydantic = schemas.encode_event(
            schemas.RagCitationEvent(
                citations=[
                    schemas.RagCitation(index=1, source="doc.md", chunk="trecho")
                ]
            )
        )
        assert nativo == pydantic
        assert _parse(nativo)["type"] == "rag_citations"

    def test_hitl_requested(self):
        nativo = to_sse_line(
            HitlRequested(
                tool_name="file_write",
                args_json="{}",
                interrupt_id="int-1",
                affected_paths=["a.py"],
            )
        )
        pydantic = schemas.encode_event(
            schemas.HITLEvent(
                tool_name="file_write",
                args_json="{}",
                interrupt_id="int-1",
                affected_paths=["a.py"],
            )
        )
        assert nativo == pydantic
        assert _parse(nativo)["type"] == "hitl"

    def test_workbench_invalidate(self):
        nativo = to_sse_line(WorkbenchInvalidate(tabs=["files", "diff"]))
        pydantic = schemas.encode_event(
            schemas.WorkbenchInvalidateEvent(tabs=["files", "diff"])
        )
        assert nativo == pydantic
        assert _parse(nativo)["type"] == "workbench_invalidate"

    def test_structured_question_requested(self):
        nativo = to_sse_line(
            StructuredQuestionRequested(
                question_id="q1",
                thread_id="t1",
                prompt="Escolha",
                options=["a", "b"],
                allow_free_text=False,
                expires_at="2030-01-01T00:00:00+00:00",
            )
        )
        pydantic = schemas.encode_event(
            schemas.StructuredQuestionEvent(
                question_id="q1",
                thread_id="t1",
                prompt="Escolha",
                options=["a", "b"],
                allow_free_text=False,
                expires_at="2030-01-01T00:00:00+00:00",
            )
        )
        assert nativo == pydantic
        assert _parse(nativo)["type"] == "structured_question"

    def test_todos_updated(self):
        nativo = to_sse_line(
            TodosUpdated(todos=[TodoItem(content="fazer x", status="in_progress")])
        )
        pydantic = schemas.encode_event(
            schemas.TodosUpdatedEvent(
                todos=[schemas.TodoItem(content="fazer x", status="in_progress")]
            )
        )
        assert nativo == pydantic
        assert _parse(nativo)["type"] == "todos_updated"

    def test_model_switched(self):
        nativo = to_sse_line(
            ModelSwitched(from_model="openai:gpt-4o", to_model="anthropic:claude")
        )
        pydantic = schemas.encode_event(
            schemas.ModelSwitchedEvent(
                from_model="openai:gpt-4o", to_model="anthropic:claude"
            )
        )
        assert nativo == pydantic
        assert _parse(nativo)["type"] == "model_switched"

    def test_ui_metrics(self):
        nativo = to_sse_line(UIMetrics(rag_hits=3, tool_calls={"file_read": 2}))
        pydantic = schemas.encode_event(
            schemas.UIMetricsEvent(rag_hits=3, tool_calls={"file_read": 2})
        )
        assert nativo == pydantic
        assert _parse(nativo)["type"] == "ui_metrics"

    def test_error_signal(self):
        nativo = to_sse_line(ErrorSignal(message="deu ruim", code="RECURSION_LIMIT"))
        pydantic = schemas.encode_event(
            schemas.ErrorEvent(message="deu ruim", code="RECURSION_LIMIT")
        )
        assert nativo == pydantic
        assert _parse(nativo)["type"] == "error"

    def test_done(self):
        nativo = to_sse_line(Done(thread_id="t1", run_id="run-1"))
        pydantic = schemas.encode_event(
            schemas.DoneEvent(thread_id="t1", run_id="run-1")
        )
        assert nativo == pydantic
        assert _parse(nativo)["type"] == "done"


class TestErroDeTipoDesconhecido:
    def test_evento_fora_do_uniao_lanca_typeerror(self):
        class _EventoFalso:
            pass

        with pytest.raises(TypeError, match="sem mapeamento"):
            to_sse_line(_EventoFalso())  # ty: ignore[invalid-argument-type]
