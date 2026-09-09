"""Regressão ao vivo: `resume_chat` (retomada de HITL) chamava
`get_user_agent(resume_user_id)` sem `model`/`chat_mode`/`workspace_id` —
caía no grafo "__default__" em vez do grafo compilado com o
`FallbackChatModel.primary_model_id` que o usuário de fato selecionou.

Migrado pro motor nativo: `stream_chat` grava o seletor (`model`,
`chat_mode`, `workspace_id`) por `thread_id` em `_thread_graph_selector`;
`resume_chat` lê esse seletor para chamar `agent_factory.get_native_agent`
com os mesmos parâmetros e monta o `FallbackChatClient` com o mesmo
`primary_model_id` do turno original.
"""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.api.schemas import ChatConfig, ResumeChatRequest, StreamChatRequest
from backend.engine.conversation_loop import LoopResult
from backend.services.agent_factory import NativeAgent
from backend.tools.registry import ToolRegistry


def _mock_registry(ws_id: str) -> MagicMock:
    ws = MagicMock()
    ws.id = ws_id
    registry = MagicMock()
    registry.get.return_value = None
    registry.get_active.return_value = None
    registry.get_or_create_session_workspace.return_value = ws
    return registry


def _fake_session_store() -> AsyncMock:
    store = AsyncMock()
    store.create_session = AsyncMock()
    store.get_branch_head_id = AsyncMock(return_value=1)
    store.append_message = AsyncMock(return_value=2)
    store.set_branch_head = AsyncMock()
    store.get_pending_approval = AsyncMock(
        return_value={
            "interrupt_id": "irrelevant",
            "tool_name": "file_write",
            "tool_call_id": "call-1",
            "args": {},
            "reasoning": None,
            "created_at": "2026-01-01T00:00:00Z",
        }
    )
    store.get_history = AsyncMock(return_value=[])
    return store


class TestResumeChatUsesSameNativeAgentAsStreamChat:
    @pytest.mark.asyncio
    async def test_resume_chat_repassa_model_chat_mode_e_workspace_do_turno_original(
        self,
    ) -> None:
        get_native_agent_calls: list[dict[str, object]] = []

        async def _fake_get_native_agent(user_id, **kwargs):
            get_native_agent_calls.append({"user_id": user_id, **kwargs})
            return NativeAgent(
                tool_registry=ToolRegistry(), subagent_catalog={}, system_prompt="p"
            )

        session_store = _fake_session_store()

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "backend.services.agent_factory.get_native_agent",
                    new=AsyncMock(side_effect=_fake_get_native_agent),
                )
            )
            stack.enter_context(
                patch(
                    "backend.services.agent_factory.get_session_store",
                    new=AsyncMock(return_value=session_store),
                )
            )
            stack.enter_context(
                patch(
                    "backend.services.agent_factory.get_approval_gate",
                    new=AsyncMock(return_value=AsyncMock()),
                )
            )
            stack.enter_context(
                patch(
                    "backend.services.agent_factory.get_store",
                    new=AsyncMock(return_value=None),
                )
            )
            stack.enter_context(
                patch(
                    "backend.api.handlers.threads._assert_owns_thread",
                    new=AsyncMock(),
                )
            )
            stack.enter_context(
                patch(
                    "backend.api.handlers.chat.run_conversation",
                    new=AsyncMock(return_value=LoopResult(stopped_reason="stop")),
                )
            )
            stack.enter_context(
                patch(
                    "backend.api.handlers.chat.resume_conversation",
                    new=AsyncMock(return_value=True),
                )
            )
            stack.enter_context(
                patch(
                    "backend.api.handlers.threads._upsert_session",
                    new=AsyncMock(),
                )
            )
            stack.enter_context(
                patch(
                    "backend.api.handlers.threads._assert_owns_thread",
                    new=AsyncMock(),
                )
            )
            stack.enter_context(
                patch(
                    "backend.workspace.workspace.workspace_registry",
                    _mock_registry("ws-resume-test"),
                )
            )

            import backend.api.handlers.chat as chat_mod

            http_request = MagicMock()
            http_request.state = MagicMock(user=None)

            stream_request = StreamChatRequest(
                content="oi",
                thread_id="thread-resume-1",
                config=ChatConfig(model="nine_router:gemini-2.5-flash"),
            )
            stream_response = await chat_mod.stream_chat(stream_request, http_request)
            async for _chunk in stream_response.body_iterator:
                pass

            resume_request = ResumeChatRequest(
                thread_id="thread-resume-1",
                interrupt_id="irrelevant",
                decision="approve",
            )
            resume_response = await chat_mod.resume_chat(resume_request, http_request)
            async for _chunk in resume_response.body_iterator:
                pass

        assert len(get_native_agent_calls) == 2
        stream_call, resume_call = get_native_agent_calls

        assert stream_call["chat_mode"] is False
        assert resume_call["chat_mode"] == stream_call["chat_mode"]
        assert resume_call["workspace_id"] == stream_call["workspace_id"]

    @pytest.mark.asyncio
    async def test_resume_chat_sem_turno_anterior_conhecido_usa_default_sem_lancar(
        self,
    ) -> None:
        """Erro/borda: thread nunca vista por stream_chat (processo reiniciado
        entre o pedido de aprovação e a resposta, ou request malformada) não
        deve lançar — degrada pro NativeAgent default em vez de quebrar o
        resume."""
        get_native_agent_calls: list[dict[str, object]] = []

        async def _fake_get_native_agent(user_id, **kwargs):
            get_native_agent_calls.append({"user_id": user_id, **kwargs})
            return NativeAgent(
                tool_registry=ToolRegistry(), subagent_catalog={}, system_prompt="p"
            )

        session_store = _fake_session_store()
        session_store.get_pending_approval = AsyncMock(return_value=None)

        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    "backend.services.agent_factory.get_native_agent",
                    new=AsyncMock(side_effect=_fake_get_native_agent),
                )
            )
            stack.enter_context(
                patch(
                    "backend.services.agent_factory.get_session_store",
                    new=AsyncMock(return_value=session_store),
                )
            )
            stack.enter_context(
                patch(
                    "backend.services.agent_factory.get_approval_gate",
                    new=AsyncMock(return_value=AsyncMock()),
                )
            )
            stack.enter_context(
                patch(
                    "backend.services.agent_factory.get_store",
                    new=AsyncMock(return_value=None),
                )
            )
            stack.enter_context(
                patch(
                    "backend.api.handlers.threads._assert_owns_thread",
                    new=AsyncMock(),
                )
            )
            import backend.api.handlers.chat as chat_mod

            http_request = MagicMock()
            http_request.state = MagicMock(user=None)

            resume_request = ResumeChatRequest(
                thread_id="thread-nunca-vista",
                interrupt_id="irrelevant",
                decision="approve",
            )
            resume_response = await chat_mod.resume_chat(resume_request, http_request)
            async for _chunk in resume_response.body_iterator:
                pass

        assert len(get_native_agent_calls) == 1
        assert get_native_agent_calls[0]["chat_mode"] is False
        assert get_native_agent_calls[0]["workspace_id"] is None
