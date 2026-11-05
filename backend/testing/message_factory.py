from backend.vtypes.message import ContentBlock, MessageRole, VMessage
from backend.vtypes.message import ToolCall as VToolCall

# ---------------------------------------------------------------------------
# Factories nativas — VMessage.
# ---------------------------------------------------------------------------


def make_user_message(text: str, images: list[str] | None = None) -> VMessage:
    """Cria uma VMessage de usuário, com blocos de imagem opcionais."""
    content = [ContentBlock(kind="text", text=text)]
    content.extend(
        ContentBlock(kind="image_url", image_url=url) for url in images or []
    )
    return VMessage(role=MessageRole.USER, content=content)


def make_assistant_message(
    text: str, tool_calls: list[VToolCall] | None = None
) -> VMessage:
    """Cria uma VMessage de assistant, com tool calls opcionais já resolvidas
    (não fragmentadas — para simular o resultado final de um turno de
    streaming já acumulado)."""
    return VMessage(
        role=MessageRole.ASSISTANT,
        content=[ContentBlock(kind="text", text=text)] if text else [],
        tool_calls=tool_calls or [],
        finish_reason="tool_calls" if tool_calls else "stop",
    )


def make_tool_result(
    tool_call_id: str, content: str, *, is_error: bool = False
) -> VMessage:
    """Cria uma VMessage de resultado de tool."""
    return VMessage(
        role=MessageRole.TOOL,
        content=[ContentBlock(kind="text", text=content)],
        tool_call_id=tool_call_id,
        is_error=is_error,
    )
