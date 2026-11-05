"""``GoogleChatClient`` — chat nativo do Google Gemini, Protocol
``ChatClient``. Espelha `test_openai_chat_client.py`/
`test_anthropic_chat_client.py`, adaptado pro formato do Gemini: cada
evento SSE é o `GenerateContentResponse` inteiro (não um delta de campo
isolado), `functionCall.args` completo num único chunk, `usageMetadata`
cumulativo por chunk (usa sempre o último valor, nunca soma).
"""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel

from backend.llm.google.chat_client import GoogleChatClient
from backend.llm.google.client import GoogleGenAIClient, GoogleGenAIResponseError
from backend.tools.registry import ToolExtras, vtool
from backend.vtypes.message import (
    ContentBlock,
    MessageRole,
    ToolCall,
    VMessage,
    text_message,
)


class _ItemAninhado(BaseModel):
    """Modelo Pydantic usado como parâmetro de tool aninhado — reproduz o
    schema `$defs`/`$ref` que o Gemini rejeita (ver
    `TestToolCalling.test_tool_com_parametro_de_modelo_aninhado_...`).
    Definido em escopo de módulo porque `get_type_hints` (chamado por
    `vtool`) resolve anotações via `globalns` do módulo, não localns."""

    nome: str
    qtd: int


def _client(handler, **kwargs) -> GoogleChatClient:
    http_client = GoogleGenAIClient(
        api_key="key-test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return GoogleChatClient(model="gemini-test", client=http_client, **kwargs)


def _resposta_ok(texto: str = "Olá", **extra) -> dict:
    return {
        "candidates": [
            {"content": {"parts": [{"text": texto}]}, "finishReason": "STOP"}
        ],
        "usageMetadata": {
            "promptTokenCount": 10,
            "candidatesTokenCount": 3,
            "totalTokenCount": 13,
        },
        **extra,
    }


class TestAgenerate:
    async def test_prompt_simples_devolve_conteudo(self):
        def handler(_req):
            return httpx.Response(200, json=_resposta_ok("Olá do Gemini"))

        resultado = await _client(handler).agenerate(
            [text_message(MessageRole.USER, "oi")]
        )

        assert resultado.text() == "Olá do Gemini"
        assert resultado.role == MessageRole.ASSISTANT

    async def test_resposta_sem_candidates_vira_erro_tipado(self):
        def handler(_req):
            return httpx.Response(
                200, json={"promptFeedback": {"blockReason": "SAFETY"}}
            )

        with pytest.raises(GoogleGenAIResponseError, match="candidates"):
            await _client(handler).agenerate([text_message(MessageRole.USER, "oi")])


class TestConversaoDeMensagens:
    async def test_system_vira_systeminstruction_e_roles_traduzidos(self):
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            capturado.update(json.loads(req.content))
            return httpx.Response(200, json=_resposta_ok())

        await _client(handler).agenerate(
            [
                text_message(MessageRole.SYSTEM, "sistema"),
                text_message(MessageRole.USER, "usuário"),
                text_message(MessageRole.ASSISTANT, "assistente"),
            ]
        )

        assert capturado["systemInstruction"]["parts"][0]["text"] == "sistema"
        assert [c["role"] for c in capturado["contents"]] == ["user", "model"]

    async def test_tool_message_vira_functionresponse_role_user(self):
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            capturado.update(json.loads(req.content))
            return httpx.Response(200, json=_resposta_ok())

        await _client(handler).agenerate(
            [
                text_message(MessageRole.USER, "usuário"),
                VMessage(
                    role=MessageRole.TOOL,
                    content=[ContentBlock(kind="text", text="42")],
                    tool_call_id="call_1",
                    name="somar",
                ),
            ]
        )

        tool_content = capturado["contents"][-1]
        assert tool_content["role"] == "user"
        fr = tool_content["parts"][0]["functionResponse"]
        assert fr["name"] == "somar"
        assert fr["response"] == {"result": "42"}

    async def test_assistant_com_tool_calls_vira_functioncall_parts(self):
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            capturado.update(json.loads(req.content))
            return httpx.Response(200, json=_resposta_ok())

        mensagem = VMessage(
            role=MessageRole.ASSISTANT,
            content=[ContentBlock(kind="text", text="vou somar")],
            tool_calls=[ToolCall(id="call_1", name="somar", args={"a": 1})],
        )

        await _client(handler).agenerate(
            [text_message(MessageRole.USER, "x"), mensagem]
        )

        partes = capturado["contents"][-1]["parts"]
        assert partes[0] == {"text": "vou somar"}
        assert partes[1] == {"functionCall": {"name": "somar", "args": {"a": 1}}}

    async def test_mensagem_de_role_desconhecido_falha(self):
        def handler(_req):
            return httpx.Response(200, json=_resposta_ok())

        estranha = text_message(MessageRole.USER, "?")
        estranha.role = "extraterrestre"  # ty: ignore[invalid-assignment]

        with pytest.raises(ValueError, match="extraterrestre"):
            await _client(handler).agenerate([estranha])

    async def test_bloco_de_imagem_nao_e_descartado(self):
        """Regressão: `_to_google_contents` usava `msg.text()` pra mensagens
        `user`, que só concatena blocos `text` — um anexo de imagem
        desaparecia do payload sem erro nem log."""
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

        partes = capturado["contents"][0]["parts"]
        assert {"text": "olha essa imagem"} in partes
        assert {"inlineData": {"mimeType": "image/png", "data": "abc"}} in partes

    async def test_imagem_com_url_direta_vira_filedata(self):
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            capturado.update(json.loads(req.content))
            return httpx.Response(200, json=_resposta_ok())

        mensagem = VMessage(
            role=MessageRole.USER,
            content=[
                ContentBlock(kind="image_url", image_url="https://exemplo.com/a.png"),
            ],
        )

        await _client(handler).agenerate([mensagem])

        partes = capturado["contents"][0]["parts"]
        assert {"fileData": {"fileUri": "https://exemplo.com/a.png"}} in partes


class TestToolCalling:
    async def test_tool_spec_vira_functiondeclarations_sem_additionalproperties(self):
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            capturado.update(json.loads(req.content))
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

        declaracoes = capturado["tools"][0]["functionDeclarations"]
        assert declaracoes[0]["name"] == "buscar"
        assert "additionalProperties" not in declaracoes[0]["parameters"]

    async def test_tool_com_parametro_de_modelo_aninhado_inline_refs_em_vez_de_defs(
        self,
    ):
        """Pydantic v2 sempre emite `$defs`/`$ref` pra qualquer parâmetro
        tipado como `BaseModel` aninhado (mesmo usado uma única vez) — e a
        API do Gemini rejeita as duas chaves com 400 ("Cannot find field"),
        reproduzido ao vivo com uma tool real de 166 do agente nativo. O
        schema enviado ao Gemini precisa ter o `$ref` resolvido inline
        (cópia real do `$defs`), não só removido — removê-lo sem inlinar
        apagaria a definição do campo por completo."""
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            capturado.update(json.loads(req.content))
            return httpx.Response(200, json=_resposta_ok())

        @vtool(extras=ToolExtras())
        async def criar_itens(itens: list[_ItemAninhado]) -> str:
            """cria itens.
            Args:
                itens: lista de itens
            """
            return ""

        from backend.tools.registry import TOOL_REGISTRY

        spec = TOOL_REGISTRY.get("criar_itens")
        assert spec is not None

        await _client(handler).agenerate(
            [text_message(MessageRole.USER, "cria 2 itens")], tools=[spec]
        )

        parametros = capturado["tools"][0]["functionDeclarations"][0]["parameters"]
        assert "$defs" not in parametros
        item_schema = parametros["properties"]["itens"]["items"]
        assert "$ref" not in item_schema
        assert item_schema["properties"]["nome"]["type"] == "string"
        assert item_schema["properties"]["qtd"]["type"] == "integer"

    async def test_function_call_nao_streaming_volta_montada(self):
        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {
                                        "functionCall": {
                                            "name": "somar",
                                            "args": {"a": 1, "b": 2},
                                        }
                                    }
                                ]
                            },
                            "finishReason": "STOP",
                        }
                    ]
                },
            )

        resultado = await _client(handler).agenerate(
            [text_message(MessageRole.USER, "some 1 e 2")]
        )
        assert resultado.tool_calls == [
            ToolCall(id="somar_call", name="somar", args={"a": 1, "b": 2})
        ]
        assert resultado.finish_reason == "tool_calls"


class TestStreaming:
    async def test_chunks_de_texto_concatenam_na_ordem(self):
        corpo = (
            b'data: {"candidates":[{"content":{"parts":[{"text":"Oi"}]}}]}\n\n'
            b'data: {"candidates":[{"content":{"parts":[{"text":" mundo"}]}}]}\n\n'
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

    async def test_chunk_sem_candidates_e_com_promptfeedback_vira_erro_tipado(self):
        corpo = b'data: {"promptFeedback":{"blockReason":"SAFETY"}}\n\n'

        def handler(_req):
            return httpx.Response(
                200, content=corpo, headers={"content-type": "text/event-stream"}
            )

        with pytest.raises(GoogleGenAIResponseError, match="bloqueou"):
            _ = [
                c
                async for c in _client(handler).astream(
                    [text_message(MessageRole.USER, "oi")]
                )
            ]

    async def test_stream_sem_nenhum_chunk_util_nao_trava(self):
        def handler(_req):
            return httpx.Response(
                200,
                content=b"",
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
    """`functionCall.args` chega completo num único chunk (sem fragmentação
    parcial como Anthropic/OpenAI); `usageMetadata` aparece em CADA chunk já
    como total cumulativo — nunca soma entre chunks, só o último valor
    importa."""

    async def test_function_call_sai_completa_num_unico_chunk(self):
        corpo = (
            b'data: {"candidates":[{"content":{"parts":[{"functionCall":'
            b'{"name":"file_read","args":{"path":"a.py"}}}]}}]}\n\n'
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
        tc = chunks[0].tool_call_chunks[0]
        assert tc.name == "file_read"
        assert json.loads(tc.args_fragment) == {"path": "a.py"}

    async def test_usage_repetido_em_varios_chunks_nao_soma(self):
        corpo = (
            b'data: {"candidates":[{"content":{"parts":[{"text":"oi"}]}}],'
            b'"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":1,'
            b'"totalTokenCount":11}}\n\n'
            b'data: {"candidates":[{"content":{"parts":[{"text":"!"}]}}],'
            b'"usageMetadata":{"promptTokenCount":10,"candidatesTokenCount":3,'
            b'"totalTokenCount":13}}\n\n'
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

        # Só o último valor visto é emitido, uma vez, ao final do stream —
        # nunca um chunk de usage por evento (evitaria soma indevida).
        assert len(com_usage) == 1
        assert com_usage[0].usage == {
            "input_tokens": 10,
            "output_tokens": 3,
            "total_tokens": 13,
        }
