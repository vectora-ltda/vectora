"""Deterministic context compaction for long native conversations."""

from __future__ import annotations

from backend.services.text import text_service
from backend.vtypes.message import ContentBlock, MessageRole, VMessage


def _message_tokens(message: VMessage) -> int:
    """Estimate tokens for text, images and tool-call arguments."""
    text = message.text()
    if message.tool_calls:
        text += "\n".join(str(call.args) for call in message.tool_calls)
    if any(block.kind == "image_url" for block in message.content):
        text += " [image]"
    return max(1, text_service.count_tokens(text) + 4)


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
    messages: list[VMessage], *, max_tokens: int, enabled: bool = True
) -> list[VMessage]:
    """Keep instructions and the newest work within a deterministic budget."""
    if not enabled or max_tokens <= 0 or len(messages) < 2:
        return messages
    if sum(_message_tokens(message) for message in messages) <= max_tokens:
        return messages

    systems = [message for message in messages if message.role == MessageRole.SYSTEM]
    non_system = [message for message in messages if message.role != MessageRole.SYSTEM]
    units = _conversation_units(non_system)
    selected_units: list[list[VMessage]] = []
    used = sum(_message_tokens(message) for message in systems)
    if used > max_tokens:
        # As instruções de sistema são obrigatórias e nunca podem ser
        # descartadas para satisfazer um orçamento de histórico. Nesse caso
        # preservamos somente essas instruções e falhamos fechado para o
        # restante da conversa, mesmo que o resultado exceda max_tokens.
        return systems
    for unit in reversed(units):
        cost = sum(_message_tokens(message) for message in unit)
        if used + cost > max_tokens:
            continue
        selected_units.append(unit)
        used += cost
    selected = [message for unit in reversed(selected_units) for message in unit]
    omitted = len(non_system) - len(selected)
    result = [*systems]
    if omitted > 0:
        marker = _marker(omitted)
        if used + _message_tokens(marker) <= max_tokens:
            result.append(marker)
    result.extend(selected)
    return result
