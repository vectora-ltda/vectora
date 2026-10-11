"""``CohereChatClient`` — chat nativo do Cohere (Chat API v2, `/v2/chat`),
implementa o Protocol ``ChatClient`` (``backend/llm/base.py``), consumido
diretamente pelo motor nativo (``backend/engine/conversation_loop.py``).

Peculiaridades da Chat API v2 que o parser respeita:
- Tool result (papel `tool` no `VMessage`) vira uma mensagem de role `tool`
  com `content` em formato de lista de blocos `document` — a API não aceita
  string solta nesse campo.
- Tool call em `assistant.tool_calls[].function.arguments` é sempre uma
  string JSON (igual à OpenAI), nunca objeto.
- O formato de tool (`{"type":"function","function":{name,description,
  parameters}}`) é idêntico ao `ToolSpec.openai_schema()` — sem tradução
  própria, mesmo padrão reusado por `backend/llm/ollama/chat_client.py` e
  `backend/llm/openrouter/chat_client.py`.
- Streaming é uma máquina de eventos por `type` (`message-start`,
  `content-start/delta/end`, `tool-plan-delta`, `tool-call-start/delta/end`,
  `message-end`) — cada tool call fragmentada chega com `index` explícito
  no próprio evento, sem precisar de contador local como na OpenAI.
- `usage` só aparece no evento `message-end` (`delta.usage.tokens`), nunca
  nos eventos intermediários — diferente do Gemini (repete em todo chunk).
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from backend.llm.cohere.client import CohereClient, CohereResponseError
from backend.vtypes.contracts import UsageSnapshot
from backend.vtypes.message import (
    ContentBlock,
    MessageRole,
    ToolCall,
    ToolCallChunk,
    VMessage,
    VMessageChunk,
)

if TYPE_CHECKING:
    from backend.tools.registry import ToolSpec

logger = logging.getLogger(__name__)

_ROLE_POR_TIPO = {
    MessageRole.SYSTEM: "system",
    MessageRole.USER: "user",
    MessageRole.ASSISTANT: "assistant",
}


def _to_cohere_content(content: list[ContentBlock]) -> str | list[dict]:
    """Bloco `text` vira string simples (caso comum); qualquer bloco
    `image_url` presente força o formato de lista de partes de conteúdo —
    nunca descarta o bloco de imagem só porque concatenar texto perderia."""
    if not any(b.kind == "image_url" for b in content):
        return "".join(b.text or "" for b in content if b.kind == "text")
    partes: list[dict] = []
    for bloco in content:
        if bloco.kind == "text" and bloco.text:
            partes.append({"type": "text", "text": bloco.text})
        elif bloco.kind == "image_url" and bloco.image_url:
            partes.append({"type": "image_url", "image_url": {"url": bloco.image_url}})
    return partes


def _assistant_tool_calls(tool_calls: list[ToolCall]) -> list[dict]:
    return [
        {
            "id": tc.id,
            "type": "function",
            "function": {
                "name": tc.name,
                "arguments": json.dumps(tc.args, ensure_ascii=False),
            },
        }
        for tc in tool_calls
    ]


def _to_cohere_messages(messages: list[VMessage]) -> list[dict]:
    """Traduz ``VMessage`` pro formato `messages` da Chat API v2. Tool
    result (role TOOL) vira mensagem `tool` com `content` em lista de
    blocos `document` — o v2 não aceita string solta nesse campo."""
    traduzidas: list[dict] = []

    for msg in messages:
        if msg.role == MessageRole.TOOL:
            traduzidas.append(
                {
                    "role": "tool",
                    "tool_call_id": msg.tool_call_id or "",
                    "content": [
                        {
                            "type": "document",
                            "document": {"data": {"result": msg.text()}},
                        }
                    ],
                }
            )
            continue

        role = _ROLE_POR_TIPO.get(msg.role)
        if role is None:
            erro = f"Mensagem de role {msg.role!r} não tem equivalente no Cohere"
            raise ValueError(erro)

        if role == "assistant" and msg.tool_calls:
            entrada: dict[str, Any] = {
                "role": "assistant",
                "tool_calls": _assistant_tool_calls(msg.tool_calls),
            }
            texto = msg.text()
            if texto:
                entrada["tool_plan"] = texto
            traduzidas.append(entrada)
            continue

        traduzidas.append({"role": role, "content": _to_cohere_content(msg.content)})

    return traduzidas


def _parse_tool_calls(bruto: list) -> list[ToolCall]:
    """`arguments` inválido não derruba a resposta — vira `_parse_error`
    (mesmo padrão de `backend/llm/openai/chat_client.py`), preservando o
    texto já gerado no turno."""
    chamadas: list[ToolCall] = []
    for item in bruto or []:
        funcao = item.get("function") or {}
        args_texto = funcao.get("arguments") or "{}"
        try:
            args = json.loads(args_texto)
        except (json.JSONDecodeError, TypeError):
            args = {"_parse_error": args_texto}
        chamadas.append(
            ToolCall(id=item.get("id") or "", name=funcao.get("name", ""), args=args)
        )
    return chamadas


def _usage_metadata(usage: dict | None) -> UsageSnapshot | None:
    if not usage:
        return None
    tokens = usage.get("tokens") or {}
    entrada = int(tokens.get("input_tokens") or 0)
    saida = int(tokens.get("output_tokens") or 0)
    return {
        "input_tokens": entrada,
        "output_tokens": saida,
        "total_tokens": entrada + saida,
    }


class CohereChatClient:
    """Chat client nativo do Cohere (Chat API v2) — implementa o Protocol
    ``ChatClient`` (``backend/llm/base.py``), async-only (CLAUDE.md regra
    10)."""

    def __init__(self, model: str, client: CohereClient) -> None:
        self.model = model
        self.client = client

    def _payload(
        self,
        messages: list[VMessage],
        *,
        tools: list[ToolSpec] | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict:
        corpo: dict[str, Any] = {
            "model": self.model,
            "messages": _to_cohere_messages(messages),
        }
        if temperature is not None:
            corpo["temperature"] = temperature
        if max_tokens is not None:
            corpo["max_tokens"] = max_tokens
        if tools:
            corpo["tools"] = [t.openai_schema() for t in tools]
        return corpo

    async def agenerate(
        self,
        messages: list[VMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> VMessage:
        corpo = self._payload(
            messages, tools=tools, temperature=temperature, max_tokens=max_tokens
        )
        resposta = await self.client.chat(corpo)
        mensagem = resposta.get("message")
        if not isinstance(mensagem, dict):
            msg = (
                "Cohere respondeu sem `message` utilizável — resposta "
                f"inutilizável (id={resposta.get('id')!r})"
            )
            raise CohereResponseError(msg)

        blocos = mensagem.get("content") or []
        texto = "".join(b.get("text", "") for b in blocos if b.get("type") == "text")
        chamadas = _parse_tool_calls(mensagem.get("tool_calls") or [])

        return VMessage(
            role=MessageRole.ASSISTANT,
            content=[ContentBlock(kind="text", text=texto)] if texto else [],
            tool_calls=chamadas,
            finish_reason="tool_calls" if chamadas else "stop",
        )

    async def astream(
        self,
        messages: list[VMessage],
        *,
        tools: list[ToolSpec] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[VMessageChunk]:
        corpo = self._payload(
            messages, tools=tools, temperature=temperature, max_tokens=max_tokens
        )

        # `tool-call-start`/`tool-call-delta` trazem `index` explícito no
        # próprio evento — diferente da OpenAI, não precisa de contador
        # local nem de mapa item_id→index.
        nomes_por_indice: dict[int, str] = {}
        ids_por_indice: dict[int, str] = {}

        async for evento in self.client.stream_chat(corpo):
            tipo = evento.get("type")
            delta = evento.get("delta") or {}
            msg_delta = delta.get("message") or {}

            if tipo == "content-delta":
                texto = (msg_delta.get("content") or {}).get("text", "")
                if texto:
                    yield VMessageChunk(delta_text=texto)

            elif tipo == "tool-call-start":
                indice = evento.get("index")
                if indice is None:
                    continue
                tc = msg_delta.get("tool_calls") or {}
                funcao = tc.get("function") or {}
                nomes_por_indice[indice] = funcao.get("name", "")
                ids_por_indice[indice] = tc.get("id", "")
                yield VMessageChunk(
                    tool_call_chunks=[
                        ToolCallChunk(
                            index=indice,
                            id=ids_por_indice[indice],
                            name=nomes_por_indice[indice],
                            args_fragment=funcao.get("arguments", ""),
                        )
                    ]
                )

            elif tipo == "tool-call-delta":
                indice = evento.get("index")
                if indice is None:
                    continue
                tc = msg_delta.get("tool_calls") or {}
                funcao = tc.get("function") or {}
                yield VMessageChunk(
                    tool_call_chunks=[
                        ToolCallChunk(
                            index=indice,
                            id=ids_por_indice.get(indice),
                            name=None,
                            args_fragment=funcao.get("arguments", ""),
                        )
                    ]
                )

            elif tipo == "message-end":
                usage = delta.get("usage")
                if usage:
                    yield VMessageChunk(usage=_usage_metadata(usage))
                return
