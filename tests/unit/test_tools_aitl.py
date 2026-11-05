"""Testes de `backend/tools/aitl.py` — AITL (subagente pede decisão ao pai).

Cobre happy path (aprovado/negado) + erro/borda (falha na chamada de
decisão nunca propaga, sempre volta negado) no mesmo arquivo.

`ask_parent_agent` é chamada como função async direta, usando
`FallbackChatClient` (Protocol `ChatClient` nativo).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from backend.tools.aitl import ask_parent_agent
from backend.tools.context import ToolContext
from backend.vtypes.message import ContentBlock, MessageRole, VMessage


def _fake_response(texto: str) -> VMessage:
    return VMessage(
        role=MessageRole.ASSISTANT, content=[ContentBlock(kind="text", text=texto)]
    )


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(user_id="alice", model="anthropic:claude-sonnet-4-6")


class TestAskParentAgent:
    async def test_aprovado_quando_decisao_comeca_com_approved(self, ctx):
        fake_agenerate = AsyncMock(
            return_value=_fake_response("APPROVED\nSeems safe, go ahead.")
        )
        with patch(
            "backend.llm.fallback_chat_client.FallbackChatClient.agenerate",
            fake_agenerate,
        ):
            result = json.loads(
                await ask_parent_agent(
                    reason="preciso rodar um comando de rede pra diagnosticar",
                    ctx=ctx,
                )
            )

        assert result["status"] == "ok"
        assert result["approved"] is True
        assert "Seems safe" in result["reason"]

    async def test_negado_quando_decisao_comeca_com_denied(self, ctx):
        fake_agenerate = AsyncMock(
            return_value=_fake_response("DENIED\nToo vague, ask the user instead.")
        )
        with patch(
            "backend.llm.fallback_chat_client.FallbackChatClient.agenerate",
            fake_agenerate,
        ):
            result = json.loads(
                await ask_parent_agent(
                    reason="preciso de mais acesso",
                    ctx=ctx,
                    requested_tool="terminal",
                )
            )

        assert result["status"] == "ok"
        assert result["approved"] is False
        assert "Too vague" in result["reason"]

    async def test_erro_na_chamada_de_decisao_nunca_propaga_e_nega(self, ctx):
        """Erro/borda: LLM de julgamento falhando (rede, quota, o que for)
        vira negado com motivo — nunca uma exceção não tratada que travaria
        o subagent esperando indefinidamente."""
        with patch(
            "backend.llm.fallback_chat_client.FallbackChatClient.agenerate",
            AsyncMock(side_effect=RuntimeError("quota esgotada")),
        ):
            result = json.loads(
                await ask_parent_agent(reason="qualquer coisa", ctx=ctx)
            )

        assert result["status"] == "ok"
        assert result["approved"] is False
        assert "erro interno" in result["reason"]
        assert "quota esgotada" in result["reason"]

    async def test_sem_requested_tool_ainda_funciona(self, ctx):
        """Erro/borda: requested_tool é opcional — omitir não quebra o prompt
        montado nem a chamada."""
        fake_agenerate = AsyncMock(return_value=_fake_response("APPROVED\nok"))
        with patch(
            "backend.llm.fallback_chat_client.FallbackChatClient.agenerate",
            fake_agenerate,
        ):
            result = json.loads(
                await ask_parent_agent(reason="só confirmando", ctx=ctx)
            )

        assert result["approved"] is True
        sent_messages = fake_agenerate.call_args.args[0]
        assert "Requested tool" not in sent_messages[1].text()
