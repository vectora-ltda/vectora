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
                text=f"[Context compacted deterministically: {removed} older messages omitted.]",
            )
        ],
    )


def compact_messages(
    messages: list[VMessage], *, max_tokens: int, enabled: bool = True
) -> list[VMessage]:
    """Keep instructions and the newest work within a deterministic budget."""
    if not enabled or max_tokens <= 0 or len(messages) < 2:
        return messages
    if sum(_message_tokens(message) for message in messages) <= max_tokens:
        return messages

    systems = [message for message in messages if message.role == MessageRole.SYSTEM]
    selected: list[VMessage] = []
    used = sum(_message_tokens(message) for message in systems)
    for message in reversed(messages):
        if message.role == MessageRole.SYSTEM:
            continue
        cost = _message_tokens(message)
        if used + cost > max_tokens and selected:
            continue
        selected.append(message)
        used += cost
        if used >= max_tokens:
            break
    selected.reverse()
    omitted = len(messages) - len(systems) - len(selected)
    result = [*systems]
    if omitted > 0:
        result.append(_marker(omitted))
    result.extend(selected)
    return result
