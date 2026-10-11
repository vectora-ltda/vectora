"""Testes unitários para src/api/schemas.py e encode_event.

Cobre:
- Instanciação de todos os event types (TokenEvent, ToolCallEvent, etc.)
- encode_event: formato SSE correto, campo "type" discriminador, JSON válido
- StreamChatRequest / ChatConfig / Thread / HistoryMessage
- ToolSchema: campos obrigatórios e defaults
- Auth schemas: SignupRequest, SigninRequest, UserResponse, TokenResponse
"""

from __future__ import annotations

import json

import pytest

# ---------------------------------------------------------------------------
# Eventos SSE
# ---------------------------------------------------------------------------


class TestTokenEvent:
    def test_defaults(self):
        from backend.api.schemas import TokenEvent

        e = TokenEvent(content="hello")
        assert e.content == "hello"
        assert e.node == ""

    def test_with_node(self):
        from backend.api.schemas import TokenEvent

        e = TokenEvent(content="world", node="invoke_llm")
        assert e.node == "invoke_llm"


class TestToolCallEvent:
    def test_defaults(self):
        from backend.api.schemas import ToolCallEvent

        e = ToolCallEvent(
            tool_name="web_search",
            tool_call_id="tc-1",
            args_json='{"query": "test"}',
        )
        assert e.render_hint == "json"
        assert e.category == "general"
        assert e.destructive is False
        assert e.icon == "tool"

    def test_destructive_flag(self):
        from backend.api.schemas import ToolCallEvent

        e = ToolCallEvent(
            tool_name="file_delete",
            tool_call_id="tc-2",
            args_json="{}",
            destructive=True,
            render_hint="diff",
        )
        assert e.destructive is True
        assert e.render_hint == "diff"


class TestToolResultEvent:
    def test_success(self):
        from backend.api.schemas import ToolResultEvent

        e = ToolResultEvent(tool_call_id="tc-1", content_json='"ok"')
        assert e.is_error is False

    def test_error(self):
        from backend.api.schemas import ToolResultEvent

        e = ToolResultEvent(tool_call_id="tc-1", content_json='"err"', is_error=True)
        assert e.is_error is True


class TestNodeEvent:
    def test_started(self):
        from backend.api.schemas import NodeEvent

        e = NodeEvent(node="orchestrator", status="started")
        assert e.duration_ms == 0

    def test_finished_with_duration(self):
        from backend.api.schemas import NodeEvent

        e = NodeEvent(node="search_agent", status="finished", duration_ms=1234)
        assert e.duration_ms == 1234


class TestDoneEvent:
    def test_basic(self):
        from backend.api.schemas import DoneEvent

        e = DoneEvent(thread_id="t-1")
        assert e.thread_id == "t-1"
        assert e.run_id == ""


class TestErrorEvent:
    def test_default_code(self):
        from backend.api.schemas import ErrorEvent

        e = ErrorEvent(message="algo falhou")
        assert e.code == "INTERNAL"

    def test_custom_code(self):
        from backend.api.schemas import ErrorEvent

        e = ErrorEvent(message="timeout", code="TIMEOUT")
        assert e.code == "TIMEOUT"


class TestHITLEvent:
    def test_fields(self):
        from backend.api.schemas import HITLEvent

        e = HITLEvent(
            tool_name="file_write",
            args_json='{"path": "/tmp/x"}',
            interrupt_id="int-1",
            permission_mode="ask",
            workspace_id="ws-1",
        )
        assert e.tool_name == "file_write"
        assert e.interrupt_id == "int-1"
        assert e.permission_mode == "ask"
        assert e.workspace_id == "ws-1"


class TestMessageBreakEvent:
    def test_encode_type_discriminator(self):
        from backend.api.schemas import MessageBreakEvent, encode_event

        line = encode_event(MessageBreakEvent())
        import json

        data = json.loads(line.removeprefix("data: ").strip())
        assert data["type"] == "message_break"

    def test_no_extra_fields(self):
        from backend.api.schemas import MessageBreakEvent

        assert MessageBreakEvent().model_dump() == {}

    def test_in_serializable_list(self):
        from backend.api.schemas import MessageBreakEvent, encode_event

        line = encode_event(MessageBreakEvent())
        assert line.startswith("data: ")
        assert line.endswith("\n\n")


class TestUIMetricsEvent:
    def test_defaults(self):
        from backend.api.schemas import UIMetricsEvent

        e = UIMetricsEvent()
        assert e.last_node == ""
        assert e.rag_hits == 0
        assert e.tool_calls == {}


# ---------------------------------------------------------------------------
# encode_event
# ---------------------------------------------------------------------------


class TestEncodeEvent:
    def test_format_starts_with_data_prefix(self):
        from backend.api.schemas import TokenEvent, encode_event

        line = encode_event(TokenEvent(content="hi"))
        assert line.startswith("data: ")

    def test_format_ends_with_double_newline(self):
        from backend.api.schemas import DoneEvent, encode_event

        line = encode_event(DoneEvent(thread_id="t-1"))
        assert line.endswith("\n\n")

    def test_type_discriminator_token(self):
        from backend.api.schemas import TokenEvent, encode_event

        line = encode_event(TokenEvent(content="x"))
        data = json.loads(line.removeprefix("data: ").strip())
        assert data["type"] == "token"
        assert data["content"] == "x"

    def test_type_discriminator_tool_call(self):
        from backend.api.schemas import ToolCallEvent, encode_event

        e = ToolCallEvent(tool_name="search", tool_call_id="1", args_json="{}")
        data = json.loads(encode_event(e).removeprefix("data: ").strip())
        assert data["type"] == "tool_call"
        assert data["tool_name"] == "search"

    def test_type_discriminator_done(self):
        from backend.api.schemas import DoneEvent, encode_event

        data = json.loads(
            encode_event(DoneEvent(thread_id="t-99")).removeprefix("data: ").strip()
        )
        assert data["type"] == "done"
        assert data["thread_id"] == "t-99"

    def test_type_discriminator_error(self):
        from backend.api.schemas import ErrorEvent, encode_event

        data = json.loads(
            encode_event(ErrorEvent(message="boom")).removeprefix("data: ").strip()
        )
        assert data["type"] == "error"
        assert data["message"] == "boom"

    def test_type_discriminator_node(self):
        from backend.api.schemas import NodeEvent, encode_event

        data = json.loads(
            encode_event(NodeEvent(node="rag_agent", status="started"))
            .removeprefix("data: ")
            .strip()
        )
        assert data["type"] == "node"
        assert data["node"] == "rag_agent"

    def test_all_event_types_are_serializable(self):
        from backend.api.schemas import (
            DoneEvent,
            ErrorEvent,
            HITLEvent,
            NodeEvent,
            ThreadEvent,
            TokenEvent,
            ToolCallEvent,
            ToolResultEvent,
            UIMetricsEvent,
            encode_event,
        )

        events = [
            ThreadEvent(thread_id="t-1"),
            TokenEvent(content="hello"),
            ToolCallEvent(tool_name="t", tool_call_id="1", args_json="{}"),
            ToolResultEvent(tool_call_id="1", content_json='"ok"'),
            NodeEvent(node="n", status="started"),
            UIMetricsEvent(),
            HITLEvent(tool_name="t", args_json="{}", interrupt_id="i-1"),
            ErrorEvent(message="err"),
            DoneEvent(thread_id="t-1"),
        ]
        for ev in events:
            line = encode_event(ev)
            data = json.loads(line.removeprefix("data: ").strip())
            assert "type" in data


# ---------------------------------------------------------------------------
# Request/Response models
# ---------------------------------------------------------------------------


class TestChatModels:
    def test_stream_chat_request_defaults(self):
        from backend.api.schemas import StreamChatRequest

        r = StreamChatRequest(content="oi")
        assert r.thread_id == ""
        assert r.config.recursion_limit == 50

    def test_chat_config_defaults(self):
        from backend.api.schemas import ChatConfig

        c = ChatConfig()
        assert c.model == ""
        assert c.llm_provider == ""

    def test_thread_model(self):
        from backend.api.schemas import Thread

        t = Thread(id="t-1", created_at="2024-01-01", updated_at="2024-01-02")
        assert t.title == ""


class TestToolSchema:
    def test_defaults(self):
        from backend.api.schemas import ToolSchema

        ts = ToolSchema(name="my_tool", description="does stuff")
        assert ts.render_hint == "json"
        assert ts.category == "general"
        assert ts.destructive is False
        assert ts.icon == "tool"
        assert ts.args_schema_json == "{}"


class TestAuthSchemas:
    def test_signup_request(self):
        from backend.api.schemas import SignupRequest

        r = SignupRequest(email="a@b.com", password="pass")
        assert r.email == "a@b.com"

    def test_user_response_from_user(self):
        from backend.api.schemas import UserResponse
        from backend.rbac.auth import User

        user = User(
            id="u-1",
            email="x@x.com",
            role="member",
            created_at="2024-01-01T00:00:00+00:00",
        )
        ur = UserResponse.from_user(user)
        assert ur.id == "u-1"
        assert ur.role == "member"
        assert ur.last_login_at is None

    def test_token_response_fields(self):
        from backend.api.schemas import TokenResponse, UserResponse

        tr = TokenResponse(
            access_token="acc",
            refresh_token="ref",
            user=UserResponse(id="u", email="e@e.com", role="root", created_at="2024"),
        )
        assert tr.token_type == "bearer"
        assert tr.access_token == "acc"

    def test_has_users_response(self):
        from backend.api.schemas import HasUsersResponse

        assert HasUsersResponse(exists=True).exists is True
        assert HasUsersResponse(exists=False).exists is False

    def test_audit_entry(self):
        from backend.api.schemas import AuditEntry

        e = AuditEntry(id="a-1", action="signin", timestamp="2024-01-01")
        assert e.success is True
        assert e.metadata == {}
