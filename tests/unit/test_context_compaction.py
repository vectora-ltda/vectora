from backend.services.context_compaction import _message_tokens
from backend.vtypes.message import (
    ContentBlock,
    MessageRole,
    VMessage,
    text_message,
)


def test_message_tokens_inclui_custo_de_cada_imagem() -> None:
    text = text_message(MessageRole.USER, "pedido")
    image = VMessage(
        role=MessageRole.USER,
        content=[ContentBlock(kind="image_url", image_url="data:image/png;base64,x")],
    )

    assert _message_tokens(image) >= 1_600 + _message_tokens(text) - 1
