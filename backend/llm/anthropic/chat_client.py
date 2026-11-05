"""``AnthropicChatClient`` — chat nativo da Anthropic (Messages API),
implementa o Protocol ``ChatClient`` (``backend/llm/base.py``), consumido
diretamente pelo motor nativo (``backend/engine/conversation_loop.py``).

Peculiaridades da Messages API que o parser respeita:
- `system` é um parâmetro top-level separado, não uma mensagem em `messages`.
- Tool result (papel `tool` no `VMessage`) vira uma mensagem de role `user`
  com um bloco `tool_result` — a API não tem role `tool` própria.
- Streaming acumula por `content_block.index`: `input_json_delta` é uma
  string JSON parcial, só faz sentido dar `json.loads` completo no
  `content_block_stop` (delegado ao caller, que já acumula os fragmentos).
- `message_delta.usage` é cumulativo (o total do turno até aquele ponto),
  não um delta a somar — cada `VMessageChunk.usage` emitido substitui o
  anterior, nunca soma.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from backend.llm.anthropic.client import AnthropicClient, AnthropicResponseError
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

logger = logging.getLogger(__name__)

_ROLE_POR_TIPO = {
    MessageRole.USER: "user",
    MessageRole.ASSISTANT: "assistant",
}


def _to_anthropic_tool(spec: ToolSpec) -> dict:
    """`{"name","description","input_schema"}` — Anthropic não usa o
    envelope `{"type":"function","function":{...}}` da OpenAI."""
    convertida = spec.openai_schema()["function"]
    return {
        "name": convertida.get("name", ""),
        "description": convertida.get("description", ""),
        "input_schema": convertida.get("parameters")
        or {"type": "object", "properties": {}},
    }


def _anthropic_image_block(data_uri: str) -> dict:
    """`data:` URI (formato que `ContentBlock.image_url` sempre carrega,
    montado em ``api/handlers/chat.py``) vira bloco `base64`; qualquer outra
    string (URL http já assinada) vira bloco `url` — a Messages API aceita
    os dois `source.type`."""
    if data_uri.startswith("data:") and ";base64," in data_uri:
        media_type, _, dado = data_uri.partition(";base64,")
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type[len("data:") :],
                "data": dado,
            },
        }
    return {"type": "image", "source": {"type": "url", "url": data_uri}}


def _to_anthropic_content(content: list[ContentBlock]) -> str | list[dict]:
    """Bloco `text` vira string simples (caso comum, evita envelope de lista
    desnecessário); qualquer bloco `image_url` presente força o formato de
    lista de blocos tipados da Messages API — nunca descarta a imagem só
    porque `msg.text()` só concatena texto."""
    if not any(b.kind == "image_url" for b in content):
        return "".join(b.text or "" for b in content if b.kind == "text")
    partes: list[dict] = []
    for bloco in content:
        if bloco.kind == "text" and bloco.text:
            partes.append({"type": "text", "text": bloco.text})
        elif bloco.kind == "image_url" and bloco.image_url:
            partes.append(_anthropic_image_block(bloco.image_url))
    return partes


def _assistant_content_blocks(msg: VMessage) -> list[dict]:
    blocos: list[dict] = []
    texto = msg.text()
    if texto:
        blocos.append({"type": "text", "text": texto})
    blocos.extend(
        {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.args}
        for tc in msg.tool_calls
    )
    return blocos


def _to_anthropic_messages(messages: list[VMessage]) -> tuple[str | None, list[dict]]:
    """Extrai o `system` (concatenado, se houver mais de uma mensagem system
    — raro, mas não deve perder conteúdo) e traduz o resto pro formato
    `messages` da Anthropic. Tool result (role TOOL) vira mensagem `user`
    com bloco `tool_result` — a API não tem role `tool`."""
    partes_system: list[str] = []
    traduzidas: list[dict] = []

    for msg in messages:
        if msg.role == MessageRole.SYSTEM:
            texto = msg.text()
            if texto:
                partes_system.append(texto)
            continue

        if msg.role == MessageRole.TOOL:
            traduzidas.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": msg.tool_call_id or "",
                            "content": msg.text(),
                            **({"is_error": True} if msg.is_error else {}),
                        }
                    ],
                }
            )
            continue

        role = _ROLE_POR_TIPO.get(msg.role)
        if role is None:
            erro = f"Mensagem de role {msg.role!r} não tem equivalente na Anthropic"
            raise ValueError(erro)

        if role == "assistant" and msg.tool_calls:
            traduzidas.append(
                {"role": "assistant", "content": _assistant_content_blocks(msg)}
            )
            continue

        traduzidas.append({"role": role, "content": _to_anthropic_content(msg.content)})

    system = "\n\n".join(partes_system) if partes_system else None
    return system, traduzidas


def _usage_metadata(usage: dict | None) -> UsageSnapshot | None:
    if not usage:
        return None
    entrada = int(usage.get("input_tokens") or 0)
    saida = int(usage.get("output_tokens") or 0)
    return {
        "input_tokens": entrada,
        "output_tokens": saida,
        "total_tokens": entrada + saida,
    }


class AnthropicChatClient:
    """Chat client nativo da Anthropic (Messages API) — implementa o
    Protocol ``ChatClient``, async-only (CLAUDE.md regra 10)."""

    def __init__(self, model: str, client: AnthropicClient) -> None:
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
        system, traduzidas = _to_anthropic_messages(messages)
        corpo: dict[str, Any] = {
            "model": self.model,
            "messages": traduzidas,
            # Anthropic exige max_tokens sempre — sem um teto explícito do
            # caller, usa um default generoso em vez de deixar a API rejeitar
            # a requisição por campo obrigatório ausente.
            "max_tokens": max_tokens or 4096,
        }
        if system:
            corpo["system"] = system
        if temperature is not None:
            corpo["temperature"] = temperature
        if tools:
            corpo["tools"] = [_to_anthropic_tool(t) for t in tools]
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
        resposta = await self.client.create_message(corpo)
        blocos = resposta.get("content")
        if not isinstance(blocos, list):
            msg = (
                "Anthropic respondeu sem `content` utilizável — resposta "
                f"inutilizável (id={resposta.get('id')!r})"
            )
            raise AnthropicResponseError(msg)

        texto_partes: list[str] = []
        chamadas: list[ToolCall] = []
        for bloco in blocos:
            tipo = bloco.get("type")
            if tipo == "text":
                texto_partes.append(bloco.get("text", ""))
            elif tipo == "tool_use":
                chamadas.append(
                    ToolCall(
                        id=bloco.get("id") or "",
                        name=bloco.get("name", ""),
                        args=coerce_json_object(bloco.get("input") or {}),
                    )
                )

        texto = "".join(texto_partes)
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

        # Acumula por `content_block.index` — cada índice é `text` ou
        # `tool_use`; `tool_use` mapeia direto pro índice do ToolCallChunk
        # que o caller (backend/engine/conversation_loop.py) usa.
        tipos_por_indice: dict[int, str] = {}
        ids_por_indice: dict[int, str] = {}
        nomes_por_indice: dict[int, str] = {}
        nome_ja_emitido: set[int] = set()

        async for evento in self.client.stream_message(corpo):
            tipo_evento = evento.get("type")

            if tipo_evento == "content_block_start":
                indice = evento.get("index")
                bloco = evento.get("content_block") or {}
                if indice is None:
                    continue
                tipos_por_indice[indice] = bloco.get("type", "")
                if bloco.get("type") == "tool_use":
                    ids_por_indice[indice] = bloco.get("id", "")
                    nomes_por_indice[indice] = bloco.get("name", "")

            elif tipo_evento == "content_block_delta":
                indice = evento.get("index")
                delta = evento.get("delta") or {}
                if indice is None:
                    continue
                tipo_delta = delta.get("type")
                if tipo_delta == "text_delta":
                    texto = delta.get("text", "")
                    if texto:
                        yield VMessageChunk(delta_text=texto)
                elif tipo_delta == "input_json_delta":
                    nome = (
                        nomes_por_indice.get(indice)
                        if indice not in nome_ja_emitido
                        else None
                    )
                    nome_ja_emitido.add(indice)
                    yield VMessageChunk(
                        tool_call_chunks=[
                            ToolCallChunk(
                                index=indice,
                                id=ids_por_indice.get(indice),
                                name=nome,
                                args_fragment=delta.get("partial_json", ""),
                            )
                        ]
                    )

            elif tipo_evento == "message_delta":
                usage = evento.get("usage")
                if usage:
                    # Cumulativo — nunca somar entre chunks (diferente da
                    # OpenAI). Cada emissão substitui a anterior.
                    yield VMessageChunk(usage=_usage_metadata(usage))

            elif tipo_evento == "message_stop":
                return
