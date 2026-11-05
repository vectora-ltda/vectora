"""``CohereChatClient`` — chat nativo do Cohere (Chat API v2), Protocol
``ChatClient``. Espelha `test_openai_chat_client.py`/
`test_openrouter_chat_client.py`, adaptado pro formato do Cohere: tool
result vira mensagem `role=tool` com `content` em blocos `document`, tool
call fragmentada carrega `index` explícito no próprio evento SSE.
"""

from __future__ import annotations

import json

import httpx
import pytest

from backend.llm.cohere.chat_client import CohereChatClient
from backend.llm.cohere.client import CohereClient, CohereResponseError
from backend.tools.registry import ToolExtras, vtool
from backend.vtypes.message import (
    ContentBlock,
    MessageRole,
    ToolCall,
    VMessage,
    text_message,
)


def _client(handler, **kwargs) -> CohereChatClient:
    http_client = CohereClient(
        api_key="co-test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return CohereChatClient(model="command-a-03-2025", client=http_client, **kwargs)


def _resposta_ok(texto: str = "Olá", **extra) -> dict:
    return {
        "id": "chat_1",
        "finish_reason": "COMPLETE",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": texto}],
        },
        "usage": {"tokens": {"input_tokens": 10, "output_tokens": 3}},
        **extra,
    }


class TestAgenerate:
    async def test_prompt_simples_devolve_conteudo(self):
        def handler(_req):
            return httpx.Response(200, json=_resposta_ok("Olá do Cohere"))

        resultado = await _client(handler).agenerate(
            [text_message(MessageRole.USER, "oi")]
        )

        assert resultado.text() == "Olá do Cohere"
        assert resultado.role == MessageRole.ASSISTANT

    async def test_resposta_sem_message_vira_erro_tipado(self):
        def handler(_req):
            return httpx.Response(200, json={"id": "chat_1"})

        with pytest.raises(CohereResponseError, match="message"):
            await _client(handler).agenerate([text_message(MessageRole.USER, "oi")])


class TestConversaoDeMensagens:
    async def test_roles_traduzidos_e_tool_message_vira_bloco_document(self):
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            capturado.update(json.loads(req.content))
            return httpx.Response(200, json=_resposta_ok())

        await _client(handler).agenerate(
            [
                text_message(MessageRole.SYSTEM, "sistema"),
                text_message(MessageRole.USER, "usuário"),
                text_message(MessageRole.ASSISTANT, "assistente"),
                VMessage(
                    role=MessageRole.TOOL,
                    content=[ContentBlock(kind="text", text="resultado")],
                    tool_call_id="call_1",
                ),
            ]
        )

        roles = [m["role"] for m in capturado["messages"]]
        assert roles == ["system", "user", "assistant", "tool"]

        tool_msg = capturado["messages"][-1]
        assert tool_msg["tool_call_id"] == "call_1"
        assert tool_msg["content"] == [
            {"type": "document", "document": {"data": {"result": "resultado"}}}
        ]

    async def test_assistant_com_tool_calls_serializa_arguments_como_json(self):
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            capturado.update(json.loads(req.content))
            return httpx.Response(200, json=_resposta_ok())

        mensagem = VMessage(
            role=MessageRole.ASSISTANT,
            content=[ContentBlock(kind="text", text="vou somar")],
            tool_calls=[ToolCall(id="call_1", name="somar", args={"a": 1, "b": 2})],
        )

        await _client(handler).agenerate(
            [text_message(MessageRole.USER, "some 1 e 2"), mensagem]
        )

        assistente = capturado["messages"][-1]
        assert assistente["tool_plan"] == "vou somar"
        assert assistente["tool_calls"] == [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "somar", "arguments": '{"a": 1, "b": 2}'},
            }
        ]

    async def test_bloco_de_imagem_nao_e_descartado(self):
        """Regressão: `_to_cohere_content` precisa preservar blocos
        `image_url` — concatenar só texto os descartaria silenciosamente."""
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            capturado.update(json.loads(req.content))
            return httpx.Response(200, json=_resposta_ok())

        mensagem = VMessage(
            role=MessageRole.USER,
            content=[
                ContentBlock(kind="text", text="olha essa imagem"),
                ContentBlock(kind="image_url", image_url="data:image/png;base64,abc"),
            ],
        )

        await _client(handler).agenerate([mensagem])

        partes = capturado["messages"][0]["content"]
        assert {"type": "text", "text": "olha essa imagem"} in partes
        assert {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,abc"},
        } in partes

    async def test_mensagem_de_role_desconhecido_falha(self):
        def handler(_req):
            return httpx.Response(200, json=_resposta_ok())

        estranha = text_message(MessageRole.USER, "?")
        estranha.role = "extraterrestre"  # ty: ignore[invalid-assignment]

        with pytest.raises(ValueError, match="extraterrestre"):
            await _client(handler).agenerate([estranha])


class TestToolCalling:
    async def test_tool_spec_vira_schema_openai_no_payload(self):
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            capturado.update(json.loads(req.content))
            return httpx.Response(200, json=_resposta_ok())

        @vtool(extras=ToolExtras())
        async def buscar(query: str) -> str:
            """busca algo.
            Args:
                query: termo
            """
            return ""

        from backend.tools.registry import TOOL_REGISTRY

        spec = TOOL_REGISTRY.get("buscar")
        assert spec is not None

        await _client(handler).agenerate(
            [text_message(MessageRole.USER, "busca x")], tools=[spec]
        )

        tool = capturado["tools"][0]
        assert tool["type"] == "function"
        assert tool["function"]["name"] == "buscar"

    async def test_tool_call_nao_streaming_volta_montada(self):
        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "id": "chat_1",
                    "finish_reason": "TOOL_CALL",
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "somar",
                                    "arguments": '{"a": 1, "b": 2}',
                                },
                            }
                        ],
                    },
                },
            )

        resultado = await _client(handler).agenerate(
            [text_message(MessageRole.USER, "some 1 e 2")]
        )
        assert resultado.tool_calls == [
            ToolCall(id="call_1", name="somar", args={"a": 1, "b": 2})
        ]
        assert resultado.finish_reason == "tool_calls"

    async def test_arguments_invalido_nao_derruba_a_resposta(self):
        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "id": "chat_1",
                    "finish_reason": "TOOL_CALL",
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "somar",
                                    "arguments": "{isso nao e json",
                                },
                            }
                        ],
                    },
                },
            )

        resultado = await _client(handler).agenerate(
            [text_message(MessageRole.USER, "x")]
        )
        assert resultado.tool_calls[0].name == "somar"
        assert "_parse_error" in resultado.tool_calls[0].args


class TestStreaming:
    async def test_chunks_de_texto_saem_na_ordem(self):
        corpo = (
            b'data: {"type":"message-start","delta":{"message":{"role":"assistant"}}}\n\n'
            b'data: {"type":"content-delta","index":0,"delta":'
            b'{"message":{"content":{"text":"Oi"}}}}\n\n'
            b'data: {"type":"content-delta","index":0,"delta":'
            b'{"message":{"content":{"text":" mundo"}}}}\n\n'
            b'data: {"type":"message-end","delta":{"finish_reason":"COMPLETE"}}\n\n'
        )

        def handler(_req):
            return httpx.Response(
                200, content=corpo, headers={"content-type": "text/event-stream"}
            )

        chunks = [
            c
            async for c in _client(handler).astream(
                [text_message(MessageRole.USER, "oi")]
            )
        ]
        texto = "".join(c.delta_text for c in chunks)

        assert texto == "Oi mundo"

    async def test_stream_sem_nenhum_chunk_util_nao_trava(self):
        def handler(_req):
            return httpx.Response(
                200,
                content=b'data: {"type":"message-end","delta":{}}\n\n',
                headers={"content-type": "text/event-stream"},
            )

        chunks = [
            c
            async for c in _client(handler).astream(
                [text_message(MessageRole.USER, "oi")]
            )
        ]
        assert chunks == []


class TestStreamingToolCallsEUsage:
    async def test_tool_call_fragmentada_por_index_sai_completa(self):
        corpo = (
            b'data: {"type":"message-start","delta":{"message":{"role":"assistant"}}}\n\n'
            b'data: {"type":"tool-call-start","index":0,"delta":{"message":'
            b'{"tool_calls":{"id":"call_1","type":"function","function":'
            b'{"name":"file_read","arguments":""}}}}}\n\n'
            b'data: {"type":"tool-call-delta","index":0,"delta":{"message":'
            b'{"tool_calls":{"function":{"arguments":"{\\"path\\":"}}}}}\n\n'
            b'data: {"type":"tool-call-delta","index":0,"delta":{"message":'
            b'{"tool_calls":{"function":{"arguments":"\\"a.py\\"}"}}}}}\n\n'
            b'data: {"type":"message-end","delta":{"finish_reason":"TOOL_CALL"}}\n\n'
        )

        def handler(_req):
            return httpx.Response(
                200, content=corpo, headers={"content-type": "text/event-stream"}
            )

        chunks = [
            c
            async for c in _client(handler).astream(
                [text_message(MessageRole.USER, "oi")]
            )
        ]
        fragments = "".join(
            tc.args_fragment for c in chunks for tc in c.tool_call_chunks
        )
        names = {tc.name for c in chunks for tc in c.tool_call_chunks if tc.name}

        assert fragments == '{"path":"a.py"}'
        assert names == {"file_read"}

    async def test_usage_do_message_end_vira_usage_metadata(self):
        corpo = (
            b'data: {"type":"content-delta","index":0,"delta":'
            b'{"message":{"content":{"text":"oi"}}}}\n\n'
            b'data: {"type":"message-end","delta":{"finish_reason":"COMPLETE",'
            b'"usage":{"tokens":{"input_tokens":10,"output_tokens":3}}}}\n\n'
        )

        def handler(_req):
            return httpx.Response(
                200, content=corpo, headers={"content-type": "text/event-stream"}
            )

        chunks = [
            c
            async for c in _client(handler).astream(
                [text_message(MessageRole.USER, "oi")]
            )
        ]
        com_usage = [c for c in chunks if c.usage]

        assert len(com_usage) == 1
        usage = com_usage[0].usage
        assert usage is not None
        assert usage["input_tokens"] == 10
        assert usage["output_tokens"] == 3
        assert usage["total_tokens"] == 13
