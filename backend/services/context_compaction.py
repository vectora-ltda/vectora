"""Deterministic context compaction for long native conversations."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace

from backend.services.image_token_cost import DEFAULT_IMAGE_TOKENS, cost_for
from backend.services.text import text_service
from backend.vtypes.message import ContentBlock, MessageRole, VMessage

_IMAGE_TOKEN_ESTIMATE = DEFAULT_IMAGE_TOKENS
_TOOL_RESULT_CONTEXT_LIMIT = 12_000

#: ``asset_id`` dos anexos cujas prévias (páginas de PDF, quadros de vídeo)
#: seguem ao modelo como imagens neste turno. Só os anexos da mensagem mais
#: recente do usuário recebem prévias (``media_hydration``); os anteriores
#: chegam só como texto e não pesam como imagem.
_PENDING_PREVIEW_ASSETS: ContextVar[frozenset[str]] = ContextVar(
    "vectora_pending_preview_assets", default=frozenset()
)


def pending_preview_assets(messages: list[VMessage]) -> frozenset[str]:
    """Anexos da mensagem mais recente do usuário que levam prévias."""
    for message in reversed(messages):
        if message.role is MessageRole.USER:
            return frozenset(
                block.asset_id
                for block in message.content
                if block.kind == "media"
                and block.asset_id
                and block.media_preview_asset_ids
            )
    return frozenset()


#: Tokens por imagem do modelo do turno (aprendidos do uso; ver
#: ``image_token_cost``). Sem modelo informado vale o padrão.
_IMAGE_TOKENS: ContextVar[int] = ContextVar(
    "vectora_image_tokens", default=DEFAULT_IMAGE_TOKENS
)


@contextmanager
def count_previews_for(
    messages: list[VMessage], model: str | None = None
) -> Generator[None]:
    """Faz a estimativa de tokens contar as prévias que serão enviadas e usar o
    custo por imagem aprendido para o ``model``."""
    previews = _PENDING_PREVIEW_ASSETS.set(pending_preview_assets(messages))
    cost = _IMAGE_TOKENS.set(cost_for(model))
    try:
        yield
    finally:
        _IMAGE_TOKENS.reset(cost)
        _PENDING_PREVIEW_ASSETS.reset(previews)


def _bound_tool_results(messages: list[VMessage]) -> list[VMessage]:
    """Keep large tool payloads available without evicting their call unit."""
    bounded: list[VMessage] = []
    for message in messages:
        if (
            message.role is not MessageRole.TOOL
            or len(message.text()) <= _TOOL_RESULT_CONTEXT_LIMIT
        ):
            bounded.append(message)
            continue
        remaining = _TOOL_RESULT_CONTEXT_LIMIT
        blocks: list[ContentBlock] = []
        for block in message.content:
            if not block.is_textual or not block.text:
                blocks.append(block)
                continue
            excerpt = block.text[:remaining]
            remaining -= len(excerpt)
            blocks.append(replace(block, text=excerpt))
            if remaining <= 0:
                break
        blocks.append(
            ContentBlock(
                kind="text",
                text="\n[resultado de ferramenta truncado para manter o contexto]",
            )
        )
        bounded.append(replace(message, content=blocks))
    return bounded


def _message_tokens(message: VMessage) -> int:
    """Estimate tokens for text, images and tool-call arguments."""
    if message.role is MessageRole.SYSTEM and message.text() == "[Context compacted]":
        # O marcador é um token de controle sintético; contar o texto inteiro
        # faria a sinalização expulsar uma unidade tool-call válida do limite.
        return 1
    text = message.model_text()
    if message.tool_calls:
        text += "\n".join(str(call.args) for call in message.tool_calls)
    image_count = sum(block.kind == "image_url" for block in message.content)
    pending = _PENDING_PREVIEW_ASSETS.get()
    if pending:
        image_count += sum(
            len(block.media_preview_asset_ids or ())
            for block in message.content
            if block.kind == "media" and block.asset_id in pending
        )
    return max(
        1, text_service.count_tokens(text) + 4 + image_count * _IMAGE_TOKENS.get()
    )


def _marker(removed: int) -> VMessage:
    return VMessage(
        role=MessageRole.SYSTEM,
        content=[
            ContentBlock(
                kind="text",
                text="[Context compacted]",
            )
        ],
    )


def _conversation_units(messages: list[VMessage]) -> list[list[VMessage]]:
    """Group assistant tool calls with their tool results as one unit.

    Providers reject histories that contain a tool result without the assistant
    call that requested it, so compaction must never split that pair.
    """
    units: list[list[VMessage]] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        unit = [message]
        index += 1
        if message.role is MessageRole.ASSISTANT and message.tool_calls:
            call_ids = {call.id for call in message.tool_calls}
            while index < len(messages):
                candidate = messages[index]
                if (
                    candidate.role is not MessageRole.TOOL
                    or candidate.tool_call_id not in call_ids
                ):
                    break
                unit.append(candidate)
                index += 1
        units.append(unit)
    return units


def compact_messages(
    messages: list[VMessage],
    *,
    max_tokens: int,
    enabled: bool = True,
    preserve_latest_tool_result: bool = False,
) -> list[VMessage]:
    """Keep instructions and the newest work within a deterministic budget."""
    if not enabled or max_tokens <= 0 or len(messages) < 2:
        return messages
    messages = _bound_tool_results(messages)
    if sum(_message_tokens(message) for message in messages) <= max_tokens:
        return messages

    systems = [message for message in messages if message.role == MessageRole.SYSTEM]
    non_system = [message for message in messages if message.role != MessageRole.SYSTEM]
    units = _conversation_units(non_system)
    selected_units: list[list[VMessage]] = []
    used = sum(_message_tokens(message) for message in systems)
    if used > max_tokens:
        if preserve_latest_tool_result:
            latest_tool_unit = next(
                (
                    unit
                    for unit in reversed(units)
                    if any(item.role is MessageRole.TOOL for item in unit)
                ),
                None,
            )
            if latest_tool_unit is not None:
                # Local providers compact the system prompt and tool result at
                # request-building time; retaining this unit prevents a large
                # result from being silently dropped before the next turn.
                return [*systems, *latest_tool_unit]
        # As instruções de sistema são obrigatórias e nunca podem ser
        # descartadas para satisfazer um orçamento de histórico. Nesse caso
        # preservamos somente essas instruções e falhamos fechado para o
        # restante da conversa, mesmo que o resultado exceda max_tokens.
        return systems
    marker_cost = _message_tokens(_marker(len(non_system)))
    selection_budget = max_tokens - marker_cost
    if used > selection_budget:
        # Não há espaço para uma unidade recente sem perder o marcador. Os
        # sistemas continuam obrigatórios; o marcador ainda cabe quando o
        # orçamento comporta ambos.
        return (
            [*systems, _marker(len(non_system))]
            if used + marker_cost <= max_tokens
            else systems
        )
    for unit in reversed(units):
        cost = sum(_message_tokens(message) for message in unit)
        if used + cost > selection_budget:
            continue
        selected_units.append(unit)
        used += cost
    selected = [message for unit in reversed(selected_units) for message in unit]
    omitted = len(non_system) - len(selected)
    result = [*systems]
    if omitted > 0:
        result.append(_marker(omitted))
    result.extend(selected)
    return result
