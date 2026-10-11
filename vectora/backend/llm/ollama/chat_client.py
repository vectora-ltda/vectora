"""``OllamaChatClient`` — chat nativo do Ollama (``POST /api/chat``),
implementa o Protocol ``ChatClient`` (``backend/llm/base.py``), consumido
diretamente pelo motor nativo (``backend/engine/conversation_loop.py``).

Dois invariantes da API do Ollama que o parser respeita:
- `thinking` nunca entra no texto da mensagem — vira `VMessageChunk.
  delta_reasoning`/bloco `reasoning`, concatenar misturaria raciocínio e
  resposta na tela.
- Imagem vai no array `images` em base64 **puro**, sem o prefixo `data:` —
  mandar no formato de content block da OpenAI faz o modelo não ver a
  imagem.

Streaming é NDJSON (não SSE) — um objeto por linha até `done: true`,
já resolvido pelo `OllamaClient.stream_ndjson`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from backend.llm.ollama.client import OllamaClient, OllamaResponseError
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


def _split_conteudo(msg: VMessage) -> tuple[str, list[str]]:
    """Devolve `(texto, imagens_base64_puras)` — o prefixo
    `data:image/...;base64,` é removido, o Ollama espera só o payload."""
    texto_partes: list[str] = []
    imagens: list[str] = []
    for bloco in msg.content:
        if bloco.kind == "text" and bloco.text:
            texto_partes.append(bloco.text)
        elif bloco.kind == "image_url" and bloco.image_url:
            bruto = bloco.image_url
            imagens.append(bruto.split(",", 1)[-1] if "," in bruto else bruto)
    return "".join(texto_partes), imagens


def _to_ollama_message(msg: VMessage) -> dict:
    role = _ROLE_POR_TIPO.get(msg.role)
    if role is None:
        erro = f"Mensagem de role {msg.role!r} não tem equivalente no Ollama"
        raise ValueError(erro)

    texto, imagens = _split_conteudo(msg)
    saida: dict[str, Any] = {"role": role, "content": texto}
    if imagens:
        saida["images"] = imagens

    if role == "assistant" and msg.tool_calls:
        saida["tool_calls"] = [
            {"function": {"name": tc.name, "arguments": tc.args}}
            for tc in msg.tool_calls
        ]
    return saida


def _parse_tool_calls(bruto: list) -> list[ToolCall]:
    """`arguments` vem como objeto no Ollama moderno e como string no
    antigo — aceitar os dois evita quebrar contra servidor desatualizado."""
    chamadas: list[ToolCall] = []
    for item in bruto or []:
        funcao = item.get("function") or {}
        args = funcao.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        chamadas.append(
            ToolCall(
                id=item.get("id") or "",
                name=funcao.get("name", ""),
                args=coerce_json_object(args),
            )
        )
    return chamadas


class OllamaChatClient:
    """Chat client nativo do Ollama — implementa o Protocol ``ChatClient``,
    async-only (CLAUDE.md regra 10)."""

    def __init__(self, model: str, client: OllamaClient) -> None:
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
        from backend.settings import settings

        corpo: dict[str, Any] = {
            "model": self.model,
            "messages": [_to_ollama_message(m) for m in messages],
        }
        options: dict[str, Any] = {
            "num_ctx": settings.ollama_num_ctx,
            "num_predict": max_tokens or settings.ollama_num_predict,
        }
        if temperature is not None:
            options["temperature"] = temperature
        corpo["options"] = options
        if tools:
            corpo["tools"] = [t.openai_schema() for t in tools]
        return corpo

    @staticmethod
    def _mensagem_da_resposta(corpo: dict) -> dict:
        mensagem = corpo.get("message")
        if not isinstance(mensagem, dict):
            msg = (
                "Ollama respondeu sem `message` — resposta inutilizável "
                f"(model={corpo.get('model')!r})"
            )
            raise OllamaResponseError(msg)
        return mensagem

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
        corpo["stream"] = False
        resposta = await self.client.post_json("/api/chat", corpo)
        mensagem = self._mensagem_da_resposta(resposta)

        texto = mensagem.get("content") or ""
        chamadas = _parse_tool_calls(mensagem.get("tool_calls") or [])
        content: list[ContentBlock] = []
        raciocinio = mensagem.get("thinking")
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
        proximo_index = 0

        async for evento in self.client.stream_ndjson("/api/chat", corpo):
            mensagem = evento.get("message") or {}

            raciocinio = mensagem.get("thinking")
            if raciocinio:
                yield VMessageChunk(delta_reasoning=raciocinio)

            texto = mensagem.get("content")
            if texto:
                yield VMessageChunk(delta_text=texto)

            for chamada in _parse_tool_calls(mensagem.get("tool_calls") or []):
                # Ollama não fragmenta argumentos de tool call — chega
                # sempre completo num único chunk, igual ao Gemini.
                yield VMessageChunk(
                    tool_call_chunks=[
                        ToolCallChunk(
                            index=proximo_index,
                            id=chamada.id or None,
                            name=chamada.name,
                            args_fragment=json.dumps(chamada.args, ensure_ascii=False),
                        )
                    ]
                )
                proximo_index += 1

            if evento.get("done"):
                entrada = int(evento.get("prompt_eval_count") or 0)
                saida = int(evento.get("eval_count") or 0)
                if entrada or saida:
                    yield VMessageChunk(
                        usage={
                            "input_tokens": entrada,
                            "output_tokens": saida,
                            "total_tokens": entrada + saida,
                        }
                    )
