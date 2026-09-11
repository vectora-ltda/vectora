"""Tipo de mensagem nativo do motor agêntico.

Fundação de todo o resto do motor nativo — o loop de conversa
(``backend/engine/conversation_loop.py``), os 5 chat clients
(``backend/llm/*/chat_client.py``) e a persistência (``backend/
persistence/native/session_store.py``) operam todos sobre ``VMessage``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

logger = logging.getLogger(__name__)


class MessageRole(StrEnum):
    """Papel da mensagem — enum nativo em vez de string solta."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(slots=True)
class ToolCall:
    """Uma chamada de tool já completa (não fragmentada) — presente em
    ``VMessage.tool_calls`` depois que o streaming termina de acumular."""

    id: str
    name: str
    args: dict[str, Any]


@dataclass(slots=True)
class ToolCallChunk:
    """Fragmento de tool call em streaming — ``args_fragment`` é a string
    JSON parcial acumulada externamente pelo caller (o próprio client de
    provider decide se emite o argumento completo num único chunk ou
    fragmentado; ``VMessageChunk`` só carrega o que já chegou)."""

    index: int
    id: str | None = None
    name: str | None = None
    args_fragment: str = ""


@dataclass(slots=True)
class ContentBlock:
    """Bloco de conteúdo multimodal — ``text`` | ``image_url`` | ``reasoning``.

    Um único campo por ``kind`` fica preenchido; os demais ficam ``None``.
    """

    kind: Literal["text", "image_url", "reasoning"]
    text: str | None = None
    image_url: str | None = None
    asset_id: str | None = None
    attachment_name: str | None = None
    reasoning_text: str | None = None


@dataclass(slots=True)
class VMessage:
    """Mensagem completa (não-streaming) do histórico de conversa."""

    role: MessageRole
    content: list[ContentBlock] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    """Presente quando ``role == MessageRole.TOOL`` — id da tool call que
    esta mensagem responde."""
    name: str | None = None
    """Nó/soul emissor (agente principal vs. subagente) — vira o campo
    `node` do evento SSE `token`."""
    finish_reason: str | None = None
    """`stop` | `length` | `content_filter` | `tool_calls` — decide se o
    loop de conversa continua ou para (backend/engine/conversation_loop.py)."""
    is_error: bool = False
    """Marca um resultado de tool como erro — vira `is_error` no evento
    SSE `tool_result`."""

    def text(self) -> str:
        """Concatena todos os blocos `text`, na ordem — equivalente ao
        `.content` string-only que o código hoje lê de `AIMessage`."""
        return "".join(b.text or "" for b in self.content if b.kind == "text")

    def to_dict(self) -> dict[str, Any]:
        """Serialização pra persistência (`SessionStore.append_message`)."""
        return {
            "role": self.role.value,
            "content": [
                {
                    "kind": b.kind,
                    "text": b.text,
                    "image_url": b.image_url,
                    "asset_id": b.asset_id,
                    "attachment_name": b.attachment_name,
                    "reasoning_text": b.reasoning_text,
                }
                for b in self.content
            ],
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "args": tc.args}
                for tc in self.tool_calls
            ],
            "tool_call_id": self.tool_call_id,
            "name": self.name,
            "finish_reason": self.finish_reason,
            "is_error": self.is_error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> VMessage:
        """Desserialização — contraparte de `to_dict`, usada por
        `SessionStore.get_history`. Linha corrompida no histórico nunca
        derruba a reconstrução inteira — vira mensagem de erro visível
        no lugar dela."""
        try:
            return cls(
                role=MessageRole(data["role"]),
                content=[
                    ContentBlock(
                        kind=b["kind"],
                        text=b.get("text"),
                        image_url=b.get("image_url"),
                        asset_id=b.get("asset_id"),
                        attachment_name=b.get("attachment_name"),
                        reasoning_text=b.get("reasoning_text"),
                    )
                    for b in data.get("content", [])
                ],
                tool_calls=[
                    ToolCall(id=tc["id"], name=tc["name"], args=tc["args"])
                    for tc in data.get("tool_calls", [])
                ],
                tool_call_id=data.get("tool_call_id"),
                name=data.get("name"),
                finish_reason=data.get("finish_reason"),
                is_error=bool(data.get("is_error", False)),
            )
        except (KeyError, ValueError) as exc:
            logger.exception("mensagem corrompida no histórico", extra={"data": data})
            return cls(
                role=MessageRole.SYSTEM,
                content=[
                    ContentBlock(
                        kind="text", text=f"[mensagem corrompida no histórico: {exc}]"
                    )
                ],
                is_error=True,
            )


def text_message(role: MessageRole, text: str, *, name: str | None = None) -> VMessage:
    """Atalho pro caso comum — mensagem de um único bloco de texto."""
    return VMessage(
        role=role, content=[ContentBlock(kind="text", text=text)], name=name
    )


@dataclass(slots=True)
class VMessageChunk:
    """Fragmento de streaming — o loop de conversa e os 5 chat clients
    (`backend/llm/*/chat.py`) emitem uma sequência desses por turno; o
    caller acumula em um `VMessage` final."""

    delta_text: str = ""
    delta_reasoning: str | None = None
    tool_call_chunks: list[ToolCallChunk] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict[str, int] | None = None
    """`{"input_tokens", "output_tokens", "total_tokens"}` — mesmo shape que
    os 5 chat clients nativos já preenchem hoje."""
