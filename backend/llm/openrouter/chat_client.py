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
from typing import TYPE_CHECKING, Any, TypedDict

from backend.llm.media_parts import (
    AcceptedMedia,
    prepare_content,
    resolve_native_media,
)
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


class _UsagePayload(TypedDict, total=False):
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


_AUDIO_FORMATS = {"audio/mpeg": "mp3", "audio/wav": "wav"}


def _media_part(bloco: ContentBlock) -> dict | None:
    """Parte Chat-Completions de um anexo enviado como arquivo."""
    data_url = bloco.media_data_url or ""
    mime_type, _, dado = data_url.partition(";base64,")
    mime_type = mime_type.removeprefix("data:")
    if mime_type == "application/pdf":
        return {
            "type": "file",
            "file": {
                "filename": bloco.attachment_name or "anexo.pdf",
                "file_data": data_url,
            },
        }
    if mime_type in _AUDIO_FORMATS:
        return {
            "type": "input_audio",
            "input_audio": {"data": dado, "format": _AUDIO_FORMATS[mime_type]},
        }
    if mime_type.startswith("video/"):
        return {"type": "video_url", "video_url": {"url": data_url}}
    return None


def _to_openrouter_content(
    content: list[ContentBlock], accepted: AcceptedMedia | None = None
) -> str | list[dict]:
    """Bloco `text` vira string simples (caso comum); imagens e anexos
    enviados como arquivo forçam o formato de lista de partes
    Chat-Completions — nunca descarta a imagem só porque `msg.text()` só
    concatena texto.

    ``accepted`` vem do catálogo do gateway para o modelo
    (:func:`backend.llm.media_parts.resolve_native_media`); sem ele, anexos
    que não são imagem seguem pela forma textual.
    """
    content = prepare_content(content, accepted or {})
    if not any(b.kind in {"image_url", "media"} for b in content):
        return "".join(b.text or "" for b in content if b.kind == "text")
    partes: list[dict] = []
    for bloco in content:
        if bloco.kind == "text" and bloco.text:
            partes.append({"type": "text", "text": bloco.text})
        elif bloco.kind == "image_url" and bloco.image_url:
            partes.append({"type": "image_url", "image_url": {"url": bloco.image_url}})
        elif bloco.kind == "media":
            parte = _media_part(bloco)
            partes.append(parte or {"type": "text", "text": bloco.text or ""})
    return partes


def _to_openrouter_message(
    msg: VMessage, accepted: AcceptedMedia | None = None
) -> dict:
    """Executa a operação  to openrouter message."""
    role = _ROLE_POR_TIPO.get(msg.role)
    if role is None:
        erro = f"Mensagem de role {msg.role!r} não tem equivalente no OpenRouter"
        raise ValueError(erro)

    saida: dict[str, Any] = {
        "role": role,
        "content": _to_openrouter_content(msg.content, accepted),
    }

    if role == "tool":
        # Obrigatório no formato OpenAI-compatível — sem ele o provider
        # recusa a mensagem de tool com 400.
        saida["tool_call_id"] = msg.tool_call_id or ""
        # Mensagem de tool só aceita texto; imagens da ferramenta seguem numa
        # mensagem de usuário logo depois (``_to_openrouter_messages``).
        saida["content"] = msg.text()

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


def _to_openrouter_messages(
    messages: list[VMessage], accepted: AcceptedMedia | None = None
) -> list[dict]:
    """Traduz o histórico e entrega imagens de ferramentas (screenshots).

    Depois de cada sequência de mensagens de tool, as imagens que elas
    trouxeram seguem numa mensagem de usuário, o formato que o Chat
    Completions aceita para imagem.
    """
    saida: list[dict] = []
    pendentes: list[dict] = []
    for msg in messages:
        if msg.role != MessageRole.TOOL and pendentes:
            saida.append({"role": "user", "content": pendentes})
            pendentes = []
        saida.append(_to_openrouter_message(msg, accepted))
        if msg.role == MessageRole.TOOL:
            imagens = [
                {"type": "image_url", "image_url": {"url": b.image_url}}
                for b in msg.content
                if b.kind == "image_url" and b.image_url
            ]
            if imagens:
                pendentes.extend(
                    [
                        {
                            "type": "text",
                            "text": f"Imagem retornada pela ferramenta {msg.name or ''}:",
                        },
                        *imagens,
                    ]
                )
    if pendentes:
        saida.append({"role": "user", "content": pendentes})
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
    """Executa a operação  parse tool call chunks."""
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


def _usage_metadata(usage: _UsagePayload | None) -> UsageSnapshot | None:
    """Executa a operação  usage metadata."""
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

    def __init__(
        self,
        model: str,
        client: OpenRouterClient,
        *,
        compact_system_prompt: bool = False,
        media_model_id: str | None = None,
    ) -> None:
        """Inicializa o estado da instância.

        ``media_model_id`` (``"openrouter:<modelo>"`` ou
        ``"nine_router:<modelo>"``) liga a consulta ao catálogo do gateway
        para enviar PDF, áudio e vídeo como arquivo aos modelos que aceitam.
        """
        self.model = model
        self.client = client
        self.compact_system_prompt = compact_system_prompt
        self.media_model_id = media_model_id

    async def _accepted_media(self, messages: list[VMessage]) -> AcceptedMedia:
        """Tipos aceitos pelo modelo, consultados só quando há arquivo."""
        if not self.media_model_id or not any(
            block.kind == "media" and block.media_data_url
            for message in messages
            for block in message.content
        ):
            return {}
        return await resolve_native_media(self.media_model_id)

    def _payload(
        self,
        messages: list[VMessage],
        *,
        tools: list[ToolSpec] | None,
        temperature: float | None,
        max_tokens: int | None,
        accepted: AcceptedMedia | None = None,
    ) -> dict:
        """Executa a operação  payload."""
        corpo: dict[str, Any] = {
            "model": self.model,
            "messages": _to_openrouter_messages(messages, accepted),
        }
        if self.compact_system_prompt:
            from backend.llm.prompt_projection import compact_small_model_system_prompt

            for message in corpo["messages"]:
                if message.get("role") == "system" and isinstance(
                    message.get("content"), str
                ):
                    message["content"] = compact_small_model_system_prompt(
                        message["content"]
                    )
                    break
        if temperature is not None:
            # Tiny local models often enter a deterministic repetition loop
            # at temperature 0 when the prompt contains identity/tool rules.
            # A small floor keeps coding deterministic enough while avoiding
            # known repetition loops in tiny local models.
            corpo["temperature"] = (
                max(0.2, temperature) if self.compact_system_prompt else temperature
            )
        if max_tokens is not None:
            corpo["max_tokens"] = max_tokens
        if tools:
            corpo["tools"] = [t.openai_schema() for t in tools]
        return corpo

    @staticmethod
    def _primeira_escolha(corpo: dict) -> dict:
        """Executa a operação  primeira escolha."""
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
        """Executa a operação agenerate."""
        corpo = self._payload(
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            accepted=await self._accepted_media(messages),
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
        """Executa a operação astream."""
        corpo = self._payload(
            messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            accepted=await self._accepted_media(messages),
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
