"""``OpenRouterChatClient`` — chat nativo do OpenRouter (`/chat/completions`,
formato OpenAI-compatível), implementa o Protocol ``ChatClient``
(``backend/llm/base.py``), consumido diretamente pelo motor nativo
(``backend/engine/conversation_loop.py``).

Peculiaridades que o parser preserva:
- `reasoning` nunca entra no texto da mensagem — canal próprio
  (`ContentBlock(kind="reasoning")`/`VMessageChunk.delta_reasoning`),
  concatenar mistura raciocínio e resposta na tela.
- `usage.cost` (campo próprio do OpenRouter, ausente na API da OpenAI) seria
  descartado por um cliente OpenAI-compat — aqui seria perdido também, já
  que `VMessage`/`VMessageChunk` não têm campo `cost`; mantido só como
  observação, não é o objetivo deste chat client feature-paridade total com
  o adapter antigo em campos que os tipos nativos não modelam ainda.
- `tool_calls` fragmentado no streaming por `index` — acumulado pelo caller
  via `ToolCallChunk`, mesmo padrão que os demais 4 clients.
- `arguments` inválido (JSON malformado) não derruba a resposta — vira
  `ToolCall(args={"_parse_error": <texto bruto>})`, mesmo padrão de
  `backend/llm/openai/chat_client.py`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from backend.llm.openrouter.client import OpenRouterClient, OpenRouterResponseError
from backend.vtypes.contracts import UsageSnapshot
from backend.vtypes.message import (
    ContentBlock,
    MessageRole,
    ToolCall,
    ToolCallChunk,
    VMessage,
    VMessageChunk,
    coerce_json_object,
)

if TYPE_CHECKING:
    from backend.tools.registry import ToolSpec

_ROLE_POR_TIPO = {
    MessageRole.SYSTEM: "system",
    MessageRole.USER: "user",
    MessageRole.ASSISTANT: "assistant",
    MessageRole.TOOL: "tool",
}


def _to_openrouter_content(content: list[ContentBlock]) -> str | list[dict]:
    """Bloco `text` vira string simples (caso comum); qualquer bloco
    `image_url` presente força o formato de lista de partes Chat-Completions
    (`image_url` aninhado) — nunca descarta a imagem só porque `msg.text()`
    só concatena texto."""
    if not any(b.kind == "image_url" for b in content):
        return "".join(b.text or "" for b in content if b.kind == "text")
    partes: list[dict] = []
    for bloco in content:
        if bloco.kind == "text" and bloco.text:
            partes.append({"type": "text", "text": bloco.text})
        elif bloco.kind == "image_url" and bloco.image_url:
            partes.append({"type": "image_url", "image_url": {"url": bloco.image_url}})
    return partes


def _to_openrouter_message(msg: VMessage) -> dict:
    role = _ROLE_POR_TIPO.get(msg.role)
    if role is None:
        erro = f"Mensagem de role {msg.role!r} não tem equivalente no OpenRouter"
        raise ValueError(erro)

    saida: dict[str, Any] = {
        "role": role,
        "content": _to_openrouter_content(msg.content),
    }

    if role == "tool":
        # Obrigatório no formato OpenAI-compatível — sem ele o provider
        # recusa a mensagem de tool com 400.
        saida["tool_call_id"] = msg.tool_call_id or ""

    if role == "assistant" and msg.tool_calls:
        saida["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": json.dumps(tc.args, ensure_ascii=False),
                },
            }
            for tc in msg.tool_calls
        ]
        saida["content"] = msg.text() or None

    return saida


def _parse_tool_calls(bruto: list) -> list[ToolCall]:
    """`arguments` inválido não derruba a resposta — vira `_parse_error`
    (mesmo padrão de `backend/llm/openai/chat_client.py`), preservando o
    texto já gerado no turno."""
    chamadas: list[ToolCall] = []
    for item in bruto or []:
        funcao = item.get("function") or {}
        nome = funcao.get("name", "")
        args_texto = funcao.get("arguments") or "{}"
        try:
            args = coerce_json_object(json.loads(args_texto), raw=args_texto)
        except (json.JSONDecodeError, TypeError):
            args = {"_parse_error": args_texto}
        chamadas.append(ToolCall(id=item.get("id") or "", name=nome, args=args))
    return chamadas


def _parse_tool_call_chunks(bruto: list) -> list[ToolCallChunk]:
    pedacos: list[ToolCallChunk] = []
    for item in bruto or []:
        funcao = item.get("function") or {}
        pedacos.append(
            ToolCallChunk(
                index=item.get("index") or 0,
                id=item.get("id"),
                name=funcao.get("name"),
                args_fragment=funcao.get("arguments") or "",
            )
        )
    return pedacos


def _usage_metadata(usage: dict | None) -> UsageSnapshot | None:
    if not usage:
        return None
    entrada = int(usage.get("prompt_tokens") or 0)
    saida = int(usage.get("completion_tokens") or 0)
    return {
        "input_tokens": entrada,
        "output_tokens": saida,
        "total_tokens": int(usage.get("total_tokens") or entrada + saida),
    }


class OpenRouterChatClient:
    """Chat client nativo do OpenRouter — implementa o Protocol
    ``ChatClient``, async-only (CLAUDE.md regra 10)."""

    def __init__(self, model: str, client: OpenRouterClient) -> None:
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
            "messages": [_to_openrouter_message(m) for m in messages],
        }
        if temperature is not None:
            corpo["temperature"] = temperature
        if max_tokens is not None:
            corpo["max_tokens"] = max_tokens
        if tools:
            corpo["tools"] = [t.openai_schema() for t in tools]
        return corpo

    @staticmethod
    def _primeira_escolha(corpo: dict) -> dict:
        escolhas = corpo.get("choices")
        if not isinstance(escolhas, list) or not escolhas:
            msg = (
                "OpenRouter respondeu sem `choices` — resposta inutilizável "
                f"(id={corpo.get('id')!r})"
            )
            raise OpenRouterResponseError(msg)
        return escolhas[0]

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
        resposta = await self.client.post_json("/chat/completions", corpo)
        escolha = self._primeira_escolha(resposta)
        mensagem = escolha.get("message") or {}

        chamadas = _parse_tool_calls(mensagem.get("tool_calls") or [])
        texto = mensagem.get("content") or ""
        content: list[ContentBlock] = []
        raciocinio = mensagem.get("reasoning")
        if raciocinio:
            content.append(ContentBlock(kind="reasoning", reasoning_text=raciocinio))
        if texto:
            content.append(ContentBlock(kind="text", text=texto))

        return VMessage(
            role=MessageRole.ASSISTANT,
            content=content,
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

        async for evento in self.client.stream_sse("/chat/completions", corpo):
            # O OpenRouter manda `usage` no último evento SSE do stream —
            # às vezes junto de `choices` vazio, às vezes acompanhando o
            # chunk final com finish_reason. Captura nos dois casos.
            usage = evento.get("usage")
            if usage:
                yield VMessageChunk(usage=_usage_metadata(usage))

            escolhas = evento.get("choices") or []
            if not escolhas:
                continue
            delta = escolhas[0].get("delta") or {}

            raciocinio = delta.get("reasoning")
            if raciocinio:
                yield VMessageChunk(delta_reasoning=raciocinio)

            tool_calls_bruto = delta.get("tool_calls")
            if tool_calls_bruto:
                yield VMessageChunk(
                    tool_call_chunks=_parse_tool_call_chunks(tool_calls_bruto)
                )

            texto = delta.get("content")
            if texto:
                yield VMessageChunk(delta_text=texto)
