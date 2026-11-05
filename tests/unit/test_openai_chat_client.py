"""``OpenAIChatClient`` — chat nativo da OpenAI (Responses API), Protocol
``ChatClient``, operando sobre ``VMessage``. Cobre o caminho crítico:
tool-calling, streaming, e o edge case confirmado pela doc oficial (`.done`
sem delta anterior).
"""

from __future__ import annotations

import httpx
import pytest

from backend.llm.openai.chat_client import OpenAIChatClient
from backend.llm.openai.client import OpenAIClient, OpenAIResponseError
from backend.tools.registry import ToolExtras, vtool
from backend.vtypes.message import (
    ContentBlock,
    MessageRole,
    ToolCall,
    VMessage,
    text_message,
)


def _client(handler, **kwargs) -> OpenAIChatClient:
    http_client = OpenAIClient(
        api_key="sk-test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return OpenAIChatClient(model="gpt-5", client=http_client, **kwargs)


def _resposta_ok(texto: str = "Olá", **extra) -> dict:
    return {
        "id": "resp_1",
        "status": "completed",
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": texto}]}
        ],
        "usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
        **extra,
    }


class TestAgenerate:
    async def test_prompt_simples_devolve_conteudo(self):
        def handler(_req):
            return httpx.Response(200, json=_resposta_ok("Olá da OpenAI"))

        resultado = await _client(handler).agenerate(
            [text_message(MessageRole.USER, "oi")]
        )

        assert resultado.text() == "Olá da OpenAI"
        assert resultado.role == MessageRole.ASSISTANT

    async def test_resposta_sem_output_vira_erro_tipado(self):
        def handler(_req):
            return httpx.Response(200, json={"id": "resp_1"})

        with pytest.raises(OpenAIResponseError, match="output"):
            await _client(handler).agenerate([text_message(MessageRole.USER, "oi")])


class TestConversaoDeMensagens:
    async def test_roles_traduzidos_e_tool_message_vira_function_call_output(self):
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            import json as _json

            capturado.update(_json.loads(req.content))
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

        tipos = [item.get("type") for item in capturado["input"]]
        roles = [item.get("role") for item in capturado["input"]]
        assert roles[:3] == ["system", "user", "assistant"]
        assert tipos[-1] == "function_call_output"
        assert capturado["input"][-1]["call_id"] == "call_1"

    async def test_bloco_de_imagem_nao_e_descartado(self):
        """Regressão: `_to_openai_input` usava `msg.text()`, que só
        concatena blocos `text` — um anexo de imagem desaparecia do payload
        sem erro nem log."""
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            import json as _json

            capturado.update(_json.loads(req.content))
            return httpx.Response(200, json=_resposta_ok())

        mensagem = VMessage(
            role=MessageRole.USER,
            content=[
                ContentBlock(kind="text", text="olha essa imagem"),
                ContentBlock(kind="image_url", image_url="data:image/png;base64,abc"),
            ],
        )

        await _client(handler).agenerate([mensagem])

        partes = capturado["input"][0]["content"]
        assert {"type": "input_text", "text": "olha essa imagem"} in partes
        assert {
            "type": "input_image",
            "image_url": "data:image/png;base64,abc",
        } in partes

    async def test_mensagem_de_role_desconhecido_falha(self):
        def handler(_req):
            return httpx.Response(200, json=_resposta_ok())

        estranha = text_message(MessageRole.USER, "?")
        estranha.role = "extraterrestre"  # ty: ignore[invalid-assignment]

        with pytest.raises(ValueError, match="extraterrestre"):
            await _client(handler).agenerate([estranha])


class TestToolCalling:
    async def test_strict_schema_normaliza_opcionais(self):
        """`strict:true` exige `additionalProperties:false` e todo campo em
        `required` — campos opcionais viram `type:[tipo, "null"]`."""
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            import json as _json

            capturado.update(_json.loads(req.content))
            return httpx.Response(200, json=_resposta_ok())

        @vtool(extras=ToolExtras())
        async def buscar(query: str, limit: int | None = None) -> str:
            """busca algo.
            Args:
                query: termo
                limit: limite
            """
            return ""

        from backend.tools.registry import TOOL_REGISTRY

        spec = TOOL_REGISTRY.get("buscar")
        assert spec is not None

        await _client(handler).agenerate(
            [text_message(MessageRole.USER, "busca x")], tools=[spec]
        )

        tool = capturado["tools"][0]
        assert tool["strict"] is True
        assert tool["parameters"]["additionalProperties"] is False
        assert set(tool["parameters"]["required"]) == {"query", "limit"}

    async def test_tool_call_nao_streaming_volta_montada(self):
        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "id": "resp_1",
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "somar",
                            "arguments": '{"a": 1, "b": 2}',
                        }
                    ],
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
                    "id": "resp_1",
                    "status": "completed",
                    "output": [
                        {
                            "type": "function_call",
                            "call_id": "call_1",
                            "name": "somar",
                            "arguments": "{isso nao e json",
                        }
                    ],
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
            b'data: {"type":"response.output_text.delta","delta":"Oi"}\n\n'
            b'data: {"type":"response.output_text.delta","delta":" mundo"}\n\n'
            b'data: {"type":"response.completed","response":{}}\n\n'
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
                content=b'data: {"type":"response.completed","response":{}}\n\n',
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
    """Caso obrigatório de regressão (achado da pesquisa da doc oficial):
    tool call fragmentada em múltiplos `response.function_call_arguments.delta`
    E o caso `.done` sem nenhum delta anterior — ambos precisam reconstruir a
    tool call completa a partir dos ToolCallChunk acumulados."""

    async def test_tool_call_fragmentada_em_varios_deltas_sai_completa(self):
        corpo = (
            b'data: {"type":"response.output_item.added","item":'
            b'{"id":"item_1","type":"function_call","name":"file_read",'
            b'"call_id":"call_1"}}\n\n'
            b'data: {"type":"response.function_call_arguments.delta",'
            b'"item_id":"item_1","delta":"{\\"path\\":"}\n\n'
            b'data: {"type":"response.function_call_arguments.delta",'
            b'"item_id":"item_1","delta":"\\"a.py\\"}"}\n\n'
            b'data: {"type":"response.function_call_arguments.done",'
            b'"item_id":"item_1","arguments":"{\\"path\\":\\"a.py\\"}"}\n\n'
            b'data: {"type":"response.completed","response":{}}\n\n'
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

    async def test_done_sem_delta_anterior_traz_json_completo(self):
        """Edge case confirmado pela doc oficial: o servidor pode pular os
        deltas e mandar o JSON completo direto no `.done`."""
        corpo = (
            b'data: {"type":"response.output_item.added","item":'
            b'{"id":"item_1","type":"function_call","name":"somar",'
            b'"call_id":"call_1"}}\n\n'
            b'data: {"type":"response.function_call_arguments.done",'
            b'"item_id":"item_1","arguments":"{\\"a\\":1,\\"b\\":2}"}\n\n'
            b'data: {"type":"response.completed","response":{}}\n\n'
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
        assert len(chunks) == 1
        assert chunks[0].tool_call_chunks[0].args_fragment == '{"a":1,"b":2}'

    async def test_usage_do_response_completed_vira_usage_metadata(self):
        corpo = (
            b'data: {"type":"response.output_text.delta","delta":"oi"}\n\n'
            b'data: {"type":"response.completed","response":'
            b'{"usage":{"input_tokens":10,"output_tokens":3,"total_tokens":13}}}\n\n'
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
