"""``OpenRouterChatClient`` — chat nativo do OpenRouter (formato OpenAI-
compatível), Protocol ``ChatClient``. Espelha os demais
`test_*_chat_client.py`: `reasoning` em canal próprio (nunca concatenado ao
texto), tool_calls fragmentado no streaming por `index`, `arguments`
inválido não derruba a resposta.
"""

from __future__ import annotations

import json

import httpx
import pytest

from backend.llm.openrouter.chat_client import OpenRouterChatClient
from backend.llm.openrouter.client import OpenRouterClient, OpenRouterResponseError
from backend.tools.registry import ToolExtras, vtool
from backend.vtypes.message import (
    ContentBlock,
    MessageRole,
    ToolCall,
    VMessage,
    text_message,
)


def _client(handler, **kwargs) -> OpenRouterChatClient:
    http_client = OpenRouterClient(
        api_key="sk-or-test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    return OpenRouterChatClient(model="openrouter/auto", client=http_client, **kwargs)


def _resposta_ok(texto: str = "Olá", **extra) -> dict:
    return {
        "id": "gen_1",
        "model": "openrouter/auto",
        "choices": [
            {
                "message": {"role": "assistant", "content": texto},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
        **extra,
    }


class TestAgenerate:
    async def test_prompt_simples_devolve_conteudo(self):
        def handler(_req):
            return httpx.Response(200, json=_resposta_ok("Olá do OpenRouter"))

        resultado = await _client(handler).agenerate(
            [text_message(MessageRole.USER, "oi")]
        )

        assert resultado.text() == "Olá do OpenRouter"
        assert resultado.role == MessageRole.ASSISTANT

    async def test_resposta_sem_choices_vira_erro_tipado(self):
        def handler(_req):
            return httpx.Response(200, json={"id": "gen_1"})

        with pytest.raises(OpenRouterResponseError, match="choices"):
            await _client(handler).agenerate([text_message(MessageRole.USER, "oi")])


class TestConversaoDeMensagens:
    async def test_projecao_de_prompt_para_modelo_pequeno_preserva_nome(self):
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            capturado.update(json.loads(req.content))
            return httpx.Response(200, json=_resposta_ok())

        client = _client(handler, compact_system_prompt=True)
        await client.agenerate(
            [
                text_message(
                    MessageRole.SYSTEM,
                    "## Identity — Vectora\n" + ("documentação longa " * 200),
                ),
                text_message(MessageRole.USER, "Quem é você?"),
            ]
        )

        system = capturado["messages"][0]["content"]
        assert "Nome: Vectora" in system
        assert len(system) < 1_000

    async def test_modelo_pequeno_aplica_piso_de_temperatura(self):
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            capturado.update(json.loads(req.content))
            return httpx.Response(200, json=_resposta_ok())

        client = _client(handler, compact_system_prompt=True)
        await client.agenerate(
            [text_message(MessageRole.USER, "apresente-se")], temperature=0
        )

        assert capturado["temperature"] == 0.2

    async def test_roles_traduzidos_e_tool_message_carrega_tool_call_id(self):
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

        mensagens = capturado["messages"]
        assert [m["role"] for m in mensagens] == ["system", "user", "assistant", "tool"]
        assert mensagens[-1]["tool_call_id"] == "call_1"

    async def test_assistant_com_tool_calls_serializa_arguments_como_json(self):
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
        assert enviado["tool_calls"][0]["function"]["name"] == "somar"
        assert json.loads(enviado["tool_calls"][0]["function"]["arguments"]) == {"a": 1}

    async def test_mensagem_de_role_desconhecido_falha(self):
        def handler(_req):
            return httpx.Response(200, json=_resposta_ok())

        estranha = text_message(MessageRole.USER, "?")
        estranha.role = "extraterrestre"  # ty: ignore[invalid-assignment]

        with pytest.raises(ValueError, match="extraterrestre"):
            await _client(handler).agenerate([estranha])

    async def test_bloco_de_imagem_nao_e_descartado(self):
        """Regressão: `_to_openrouter_message` usava `msg.text()`, que só
        concatena blocos `text` — um anexo de imagem desaparecia do payload
        sem erro nem log."""
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

        assert capturado["tools"][0]["function"]["name"] == "buscar"

    async def test_tool_call_nao_streaming_volta_montada(self):
        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "id": "gen_1",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
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
                            "finish_reason": "tool_calls",
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
                    "id": "gen_1",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
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
                            "finish_reason": "tool_calls",
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
            b'data: {"choices":[{"delta":{"content":"Oi"}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":" mundo"}}]}\n\n'
            b"data: [DONE]\n\n"
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

    async def test_reasoning_nao_entra_no_delta_text(self):
        corpo = (
            b'data: {"choices":[{"delta":{"reasoning":"pensando..."}}]}\n\n'
            b'data: {"choices":[{"delta":{"content":"resposta"}}]}\n\n'
            b"data: [DONE]\n\n"
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
        raciocinio = "".join(c.delta_reasoning or "" for c in chunks)

        assert texto == "resposta"
        assert raciocinio == "pensando..."

    async def test_stream_sem_nenhum_chunk_util_nao_trava(self):
        def handler(_req):
            return httpx.Response(
                200,
                content=b"data: [DONE]\n\n",
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
        eventos = [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "function": {"name": "file_read", "arguments": ""},
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": '{"path":'},
                                }
                            ]
                        }
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": '"a.py"}'},
                                }
                            ]
                        }
                    }
                ]
            },
        ]
        corpo = "".join(f"data: {json.dumps(e)}\n\n" for e in eventos).encode()
        corpo += b"data: [DONE]\n\n"

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

    async def test_usage_do_ultimo_evento_vira_usage_metadata(self):
        corpo = (
            b'data: {"choices":[{"delta":{"content":"oi"}}]}\n\n'
            b'data: {"choices":[],"usage":'
            b'{"prompt_tokens":10,"completion_tokens":3,"total_tokens":13}}\n\n'
            b"data: [DONE]\n\n"
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
        assert com_usage[0].usage == {
            "input_tokens": 10,
            "output_tokens": 3,
            "total_tokens": 13,
        }

    async def test_compact_system_prompt_preserva_instrucoes_e_idioma(self):
        payload_captured = {}

        def handler(req):
            nonlocal payload_captured
            payload_captured = json.loads(req.content.decode("utf-8"))
            return httpx.Response(200, json=_resposta_ok("Ok"))

        client = _client(handler, compact_system_prompt=True)
        system_text = (
            "Você é o Vectora Coder Agent com acesso ao sistema.\n"
            "<guidelines>\n"
            "Antes de alterar arquivos, solicite aprovação do usuário.\n"
            "</guidelines>\n"
            "Idioma: Responda em Espanhol.\n"
            "Regra: Nunca exclua o arquivo de banco de dados.\n"
            "Muitas descrições longas sobre a interface e botões que devem ser ignoradas..."
        )
        await client.agenerate(
            [
                text_message(MessageRole.SYSTEM, system_text),
                text_message(MessageRole.USER, "ajuda"),
            ]
        )

        sys_msg = payload_captured["messages"][0]["content"]
        assert "Antes de alterar arquivos, solicite aprovação do usuário." in sys_msg
        assert "Idioma: Responda em Espanhol." in sys_msg
        assert "Regra: Nunca exclua o arquivo de banco de dados." in sys_msg
        assert "Muitas descrições longas" not in sys_msg
