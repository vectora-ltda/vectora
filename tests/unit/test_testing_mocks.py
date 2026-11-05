"""Testes para backend/testing/mocks.py — FakeChatClient, implementação
nativa do Protocol ChatClient (backend/llm/base.py) pra scripts de teste."""

from __future__ import annotations

import pytest

from backend.testing.mocks import FakeChatClient, text_chunk, text_response
from backend.vtypes.message import MessageRole, ToolCall, ToolCallChunk, VMessageChunk


async def _collect(client: FakeChatClient, messages, **kwargs) -> list[VMessageChunk]:
    return [chunk async for chunk in client.astream(messages, **kwargs)]


class TestAstream:
    async def test_devolve_o_proximo_turno_roteirizado_a_cada_chamada(self):
        client = FakeChatClient(turns=[[text_chunk("um")], [text_chunk("dois")]])

        primeiro = await _collect(client, [])
        segundo = await _collect(client, [])

        assert [c.delta_text for c in primeiro] == ["um"]
        assert [c.delta_text for c in segundo] == ["dois"]
        assert client.stream_calls == 2

    async def test_tool_call_chunk_roteirizavel(self):
        turno = [
            VMessageChunk(
                tool_call_chunks=[
                    ToolCallChunk(index=0, id="c1", name="buscar", args_fragment="{}")
                ]
            )
        ]
        client = FakeChatClient(turns=[turno])

        chunks = await _collect(client, [])

        assert chunks[0].tool_call_chunks[0].name == "buscar"

    async def test_registra_mensagens_e_tools_recebidas(self):
        client = FakeChatClient(turns=[[text_chunk("ok")]])
        msgs = [text_response("oi")]

        await _collect(client, msgs, tools=None)

        assert len(client.calls) == 1
        assert client.calls[0]["kind"] == "astream"
        assert client.calls[0]["messages"] == msgs

    async def test_estourar_o_script_levanta_assertion_error_clara(self):
        client = FakeChatClient(turns=[[text_chunk("único turno")]])
        await _collect(client, [])

        with pytest.raises(AssertionError, match="script insuficiente"):
            await _collect(client, [])


class TestAgenerate:
    async def test_devolve_a_proxima_resposta_roteirizada(self):
        client = FakeChatClient(responses=[text_response("primeira")])

        resultado = await client.agenerate([])

        assert resultado.text() == "primeira"
        assert resultado.role == MessageRole.ASSISTANT
        assert client.generate_calls == 1

    async def test_resposta_com_tool_calls(self):
        resposta = text_response("")
        resposta.tool_calls = [ToolCall(id="1", name="somar", args={"a": 1, "b": 2})]
        client = FakeChatClient(responses=[resposta])

        resultado = await client.agenerate([])

        assert resultado.tool_calls[0].name == "somar"

    async def test_estourar_o_script_levanta_assertion_error_clara(self):
        client = FakeChatClient(responses=[])

        with pytest.raises(AssertionError, match="script insuficiente"):
            await client.agenerate([])
