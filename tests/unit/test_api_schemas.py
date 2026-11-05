"""Testes unitários para src/api/schemas.py.

Valida:
- Criação dos modelos Pydantic de request/response
- encode_event produz linhas SSE válidas
- Discriminação correta de tipos via campo 'type'
"""

from __future__ import annotations

import json

import pytest

from backend.api.schemas import (
    ChatConfig,
    CreateShareRequest,
    CreateShareResponse,
    CreateThreadRequest,
    DeleteThreadRequest,
    DoneEvent,
    ErrorEvent,
    GetHistoryRequest,
    GetHistoryResponse,
    GetThreadRequest,
    GetToolsResponse,
    HistoryMessage,
    HITLEvent,
    ListThreadsRequest,
    ListThreadsResponse,
    ModelSwitchedEvent,
    NodeEvent,
    ResumeChatRequest,
    SharedThread,
    StreamChatRequest,
    Thread,
    ThreadEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolSchema,
    UIMetricsEvent,
    encode_event,
)

# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class TestRequestModels:
    def test_stream_chat_request_defaults(self):
        req = StreamChatRequest(content="Olá")
        assert req.content == "Olá"
        assert req.thread_id == ""
        assert req.config.recursion_limit == 50

    def test_stream_chat_request_with_thread(self):
        req = StreamChatRequest(content="Teste", thread_id="abc123")
        assert req.thread_id == "abc123"

    def test_stream_chat_request_with_config(self):
        req = StreamChatRequest(
            content="x",
            config=ChatConfig(model="gpt-4", recursion_limit=25),
        )
        assert req.config.model == "gpt-4"
        assert req.config.recursion_limit == 25

    def test_resume_chat_request(self):
        req = ResumeChatRequest(thread_id="t1", interrupt_id="i1", decision="approve")
        assert req.decision == "approve"

    def test_list_threads_default_limit(self):
        req = ListThreadsRequest()
        assert req.limit == 50

    def test_get_history_request(self):
        req = GetHistoryRequest(thread_id="tid")
        assert req.thread_id == "tid"


# ---------------------------------------------------------------------------
# Event models e encode_event
# ---------------------------------------------------------------------------


class TestEventModels:
    def test_thread_event(self):
        ev = ThreadEvent(thread_id="t1")
        line = encode_event(ev)
        assert line.startswith("data: ")
        assert line.endswith("\n\n")
        data = json.loads(line[6:])
        assert data["type"] == "thread"
        assert data["thread_id"] == "t1"

    def test_token_event(self):
        ev = TokenEvent(content="Hello", node="orchestrator")
        line = encode_event(ev)
        data = json.loads(line[6:])
        assert data["type"] == "token"
        assert data["content"] == "Hello"
        assert data["node"] == "orchestrator"

    def test_tool_call_event(self):
        ev = ToolCallEvent(
            tool_name="web_search",
            tool_call_id="tc1",
            args_json='{"query": "test"}',
            render_hint="web_results",
        )
        line = encode_event(ev)
        data = json.loads(line[6:])
        assert data["type"] == "tool_call"
        assert data["tool_name"] == "web_search"
        assert data["render_hint"] == "web_results"

    def test_model_switched_event(self):
        ev = ModelSwitchedEvent(
            from_model="openai:gpt-4o",
            to_model="google-genai:gemini-2.5-flash",
        )
        line = encode_event(ev)
        data = json.loads(line[6:])
        assert data["type"] == "model_switched"
        assert data["from_model"] == "openai:gpt-4o"
        assert data["to_model"] == "google-genai:gemini-2.5-flash"

    def test_model_switched_in_type_map(self):
        from backend.api.schemas import _TYPE_MAP

        assert _TYPE_MAP[ModelSwitchedEvent] == "model_switched"

    def test_tool_result_event(self):
        ev = ToolResultEvent(
            tool_call_id="tc1",
            content_json='{"result": "ok"}',
            is_error=False,
        )
        line = encode_event(ev)
        data = json.loads(line[6:])
        assert data["type"] == "tool_result"
        assert data["is_error"] is False

    def test_node_event_started(self):
        ev = NodeEvent(node="rag_subgraph", status="started")
        line = encode_event(ev)
        data = json.loads(line[6:])
        assert data["type"] == "node"
        assert data["status"] == "started"

    def test_node_event_finished_with_duration(self):
        ev = NodeEvent(node="search", status="finished", duration_ms=350)
        line = encode_event(ev)
        data = json.loads(line[6:])
        assert data["duration_ms"] == 350

    def test_ui_metrics_event(self):
        ev = UIMetricsEvent(
            last_node="invoke_llm",
            rag_hits=3,
            rag_misses=1,
            tool_calls={"web_search": 2},
        )
        line = encode_event(ev)
        data = json.loads(line[6:])
        assert data["type"] == "ui_metrics"
        assert data["rag_hits"] == 3
        assert data["tool_calls"]["web_search"] == 2

    def test_hitl_event(self):
        ev = HITLEvent(
            tool_name="terminal",
            args_json='{"command": "rm -rf /"}',
            interrupt_id="hitl-42",
        )
        line = encode_event(ev)
        data = json.loads(line[6:])
        assert data["type"] == "hitl"
        assert data["interrupt_id"] == "hitl-42"

    def test_error_event(self):
        ev = ErrorEvent(message="Algo deu errado", code="INTERNAL")
        line = encode_event(ev)
        data = json.loads(line[6:])
        assert data["type"] == "error"
        assert data["message"] == "Algo deu errado"

    def test_done_event(self):
        ev = DoneEvent(thread_id="t1", run_id="run-99")
        line = encode_event(ev)
        data = json.loads(line[6:])
        assert data["type"] == "done"
        assert data["run_id"] == "run-99"


# ---------------------------------------------------------------------------
# Thread e History models
# ---------------------------------------------------------------------------


class TestThreadModels:
    def test_thread_model(self):
        t = Thread(
            id="abc",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T01:00:00Z",
            title="Minha Thread",
        )
        assert t.title == "Minha Thread"

    def test_thread_default_title(self):
        t = Thread(id="x", created_at="2026-01-01", updated_at="2026-01-01")
        assert t.title == ""

    def test_history_message(self):
        msg = HistoryMessage(role="human", content="Olá")
        assert msg.role == "human"
        # checkpoint_id (fork pra editar/regenerar) é opcional — vazio por
        # default, presente quando o backend resolve o checkpoint pai.
        assert msg.checkpoint_id == ""
        msg_with_cp = HistoryMessage(
            role="human", content="Olá", checkpoint_id="cp-abc"
        )
        assert msg_with_cp.checkpoint_id == "cp-abc"

    def test_list_threads_response(self):
        resp = ListThreadsResponse(
            threads=[
                Thread(id="1", created_at="2026", updated_at="2026"),
                Thread(id="2", created_at="2026", updated_at="2026"),
            ]
        )
        assert len(resp.threads) == 2

    def test_get_history_response(self):
        resp = GetHistoryResponse(
            messages=[
                HistoryMessage(role="human", content="Oi"),
                HistoryMessage(role="assistant", content="Olá!"),
            ]
        )
        assert len(resp.messages) == 2
        assert resp.messages[1].role == "assistant"


# ---------------------------------------------------------------------------
# Tool schema
# ---------------------------------------------------------------------------


class TestToolSchema:
    def test_tool_schema_defaults(self):
        ts = ToolSchema(name="web_search", description="Busca na web")
        assert ts.render_hint == "json"
        assert ts.args_schema_json == "{}"

    def test_get_tools_response(self):
        resp = GetToolsResponse(
            tools=[
                ToolSchema(
                    name="vector_search",
                    description="RAG",
                    render_hint="search_results",
                ),
            ]
        )
        assert resp.tools[0].render_hint == "search_results"


# ---------------------------------------------------------------------------
# Share schemas
# ---------------------------------------------------------------------------


class TestShareSchemas:
    def test_create_share_request_defaults(self):
        req = CreateShareRequest(thread_id="t1")
        assert req.ttl_hours == 72

    def test_create_share_request_custom_ttl(self):
        req = CreateShareRequest(thread_id="t1", ttl_hours=48)
        assert req.ttl_hours == 48

    def test_create_share_response(self):
        resp = CreateShareResponse(
            token="tok123",  # fixture
            url="http://localhost/share/tok123",
            expires_at="2026-06-07T00:00:00Z",
        )
        assert resp.token == "tok123"  # fixture
        assert "/share/" in resp.url

    def test_shared_thread_defaults(self):
        st = SharedThread(
            thread_id="t1",
            messages=[HistoryMessage(role="human", content="Oi")],
            created_at="2026-06-04T00:00:00Z",
        )
        assert st.title == ""
        assert st.expires_at == ""
        assert len(st.messages) == 1

    def test_shared_thread_with_title(self):
        st = SharedThread(
            thread_id="t1",
            title="Minha conversa",
            messages=[],
            created_at="2026-06-04T00:00:00Z",
            expires_at="2026-06-07T00:00:00Z",
        )
        assert st.title == "Minha conversa"
