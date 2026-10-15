"""``OllamaChatClient`` — chat nativo do Ollama, Protocol ``ChatClient``.
Espelha os demais `test_*_chat_client.py`, adaptado pro
formato do Ollama: streaming NDJSON (não SSE), `thinking` separado do
`content`, imagem em base64 puro sem prefixo `data:`, tool call sempre
completa num único evento (sem fragmentação).
"""

from __future__ import annotations

import json

import httpx
import pytest

from backend.llm.ollama.chat_client import OllamaChatClient, _parse_tool_calls
from backend.llm.ollama.client import OllamaClient, OllamaResponseError
from backend.tools.registry import ToolExtras, vtool
from backend.vtypes.message import (
    ContentBlock,
    MessageRole,
    ToolCall,
    VMessage,
    text_message,
)


def _client(handler, **kwargs) -> OllamaChatClient:
    http_client = OllamaClient(
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return OllamaChatClient(model="llama3", client=http_client, **kwargs)


def _resposta_ok(texto: str = "Olá", **extra) -> dict:
    return {
        "model": "llama3",
        "done": True,
        "message": {"role": "assistant", "content": texto},
        "prompt_eval_count": 10,
        "eval_count": 3,
        **extra,
    }


class TestAgenerate:
    async def test_prompt_simples_devolve_conteudo(self):
        def handler(_req):
            return httpx.Response(200, json=_resposta_ok("Olá do Ollama"))

        resultado = await _client(handler).agenerate(
            [text_message(MessageRole.USER, "oi")]
        )

        assert resultado.text() == "Olá do Ollama"
        assert resultado.role == MessageRole.ASSISTANT

    async def test_resposta_sem_message_vira_erro_tipado(self):
        def handler(_req):
            return httpx.Response(200, json={"model": "llama3", "done": True})

        with pytest.raises(OllamaResponseError, match="message"):
            await _client(handler).agenerate([text_message(MessageRole.USER, "oi")])


class TestConversaoDeMensagens:
    async def test_roles_traduzidos(self):
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

    async def test_imagem_vai_sem_prefixo_data_url(self):
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            capturado.update(json.loads(req.content))
            return httpx.Response(200, json=_resposta_ok())

        mensagem = VMessage(
            role=MessageRole.USER,
            content=[
                ContentBlock(kind="text", text="olha essa imagem"),
                ContentBlock(
                    kind="image_url", image_url="data:image/png;base64,abc123"
                ),
            ],
        )

        await _client(handler).agenerate([mensagem])

        enviado = capturado["messages"][0]
        assert enviado["content"] == "olha essa imagem"
        assert enviado["images"] == ["abc123"]

    async def test_assistant_com_tool_calls_vira_function_arguments(self):
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

        enviado = capturado["messages"][-1]
        assert enviado["tool_calls"] == [
            {"function": {"name": "somar", "arguments": {"a": 1}}}
        ]

    async def test_mensagem_de_role_desconhecido_falha(self):
        def handler(_req):
            return httpx.Response(200, json=_resposta_ok())

        estranha = text_message(MessageRole.USER, "?")
        estranha.role = "extraterrestre"  # ty: ignore[invalid-assignment]

        with pytest.raises(ValueError, match="extraterrestre"):
            await _client(handler).agenerate([estranha])


class TestToolCalling:
    @pytest.mark.parametrize("arguments", [[], 0, "not-json"])
    def test_argumentos_falsos_ou_json_invalido_viram_parse_error(
        self, arguments: list[object] | int | str
    ) -> None:
        calls = _parse_tool_calls(
            [{"function": {"name": "somar", "arguments": arguments}}]
        )

        assert calls[0].args["_parse_error"]
        if isinstance(arguments, str):
            assert calls[0].args["_parse_error"] == arguments

    async def test_tool_spec_vira_schema_no_payload(self):
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

        assert capturado["tools"][0]["function"]["name"] == "buscar"
        assert capturado["options"]["temperature"] == 0.2

    async def test_tool_call_com_arguments_string_e_desserializada(self):
        """Ollama antigo manda `arguments` como string JSON; o moderno já
        manda objeto — os dois precisam funcionar sem estourar."""

        def handler(_req):
            return httpx.Response(
                200,
                json=_resposta_ok(
                    message={
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "function": {
                                    "name": "somar",
                                    "arguments": '{"a": 1, "b": 2}',
                                }
                            }
                        ],
                    },
                ),
            )

        resultado = await _client(handler).agenerate(
            [text_message(MessageRole.USER, "some 1 e 2")]
        )
        assert resultado.tool_calls == [
            ToolCall(id="", name="somar", args={"a": 1, "b": 2})
        ]
        assert resultado.finish_reason == "tool_calls"


class TestStreaming:
    async def test_chunks_de_texto_saem_na_ordem(self):
        corpo = (
            b'{"message":{"role":"assistant","content":"Oi"},"done":false}\n'
            b'{"message":{"role":"assistant","content":" mundo"},"done":false}\n'
            b'{"message":{"role":"assistant","content":""},"done":true,'
            b'"prompt_eval_count":10,"eval_count":3}\n'
        )

        def handler(_req):
            return httpx.Response(
                200, content=corpo, headers={"content-type": "application/x-ndjson"}
            )

        chunks = [
            c
            async for c in _client(handler).astream(
                [text_message(MessageRole.USER, "oi")]
            )
        ]
        texto = "".join(c.delta_text for c in chunks)

        assert texto == "Oi mundo"

    async def test_thinking_nao_entra_no_delta_text(self):
        corpo = (
            b'{"message":{"role":"assistant","content":"","thinking":"pensando..."},'
            b'"done":false}\n'
            b'{"message":{"role":"assistant","content":"resposta"},"done":true}\n'
        )

        def handler(_req):
            return httpx.Response(
                200, content=corpo, headers={"content-type": "application/x-ndjson"}
            )

        chunks = [
            c
            async for c in _client(handler).astream(
                [text_message(MessageRole.USER, "oi")]
            )
        ]
        texto = "".join(c.delta_text for c in chunks)
        raciocinio = "".join(c.delta_reasoning or "" for c in chunks)

        assert texto == "resposta"
        assert raciocinio == "pensando..."

    async def test_stream_sem_nenhum_chunk_util_nao_trava(self):
        def handler(_req):
            return httpx.Response(
                200,
                content=b'{"message":{"role":"assistant","content":""},"done":true}\n',
                headers={"content-type": "application/x-ndjson"},
            )

        chunks = [
            c
            async for c in _client(handler).astream(
                [text_message(MessageRole.USER, "oi")]
            )
        ]
        assert chunks == []


class TestStreamingToolCallsEUsage:
    async def test_tool_call_sai_completa_num_unico_chunk(self):
        corpo = (
            b'{"message":{"role":"assistant","content":"","tool_calls":'
            b'[{"function":{"name":"file_read","arguments":{"path":"a.py"}}}]},'
            b'"done":false}\n'
            b'{"message":{"role":"assistant","content":""},"done":true,'
            b'"prompt_eval_count":10,"eval_count":3}\n'
        )

        def handler(_req):
            return httpx.Response(
                200, content=corpo, headers={"content-type": "application/x-ndjson"}
            )

        chunks = [
            c
            async for c in _client(handler).astream(
                [text_message(MessageRole.USER, "oi")]
            )
        ]
        tool_chunks = [tc for c in chunks for tc in c.tool_call_chunks]

        assert len(tool_chunks) == 1
        assert tool_chunks[0].name == "file_read"
        assert json.loads(tool_chunks[0].args_fragment) == {"path": "a.py"}

    async def test_usage_so_emitido_no_evento_done(self):
        corpo = (
            b'{"message":{"role":"assistant","content":"oi"},"done":false}\n'
            b'{"message":{"role":"assistant","content":""},"done":true,'
            b'"prompt_eval_count":10,"eval_count":3}\n'
        )

        def handler(_req):
            return httpx.Response(
                200, content=corpo, headers={"content-type": "application/x-ndjson"}
            )

        chunks = [
            c
            async for c in _client(handler).astream(
                [text_message(MessageRole.USER, "oi")]
            )
        ]
        com_usage = [c for c in chunks if c.usage]

        assert len(com_usage) == 1
        assert com_usage[0].usage == {
            "input_tokens": 10,
            "output_tokens": 3,
            "total_tokens": 13,
        }
