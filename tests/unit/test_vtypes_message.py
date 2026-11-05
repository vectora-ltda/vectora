"""``backend/vtypes/message.py`` — tipo de mensagem nativo do motor agêntico."""

from __future__ import annotations

from backend.vtypes.message import (
    ContentBlock,
    MessageRole,
    ToolCall,
    ToolCallChunk,
    VMessage,
    VMessageChunk,
    text_message,
)


class TestVMessageText:
    def test_concatena_multiplos_blocos_de_texto_na_ordem(self):
        msg = VMessage(
            role=MessageRole.ASSISTANT,
            content=[
                ContentBlock(kind="text", text="Olá, "),
                ContentBlock(kind="image_url", image_url="data:image/png;base64,xyz"),
                ContentBlock(kind="text", text="mundo"),
            ],
        )
        assert msg.text() == "Olá, mundo"

    def test_texto_vazio_quando_so_ha_blocos_nao_texto(self):
        """Erro/borda: mensagem só com imagem não deve quebrar `.text()`,
        deve devolver string vazia."""
        msg = VMessage(
            role=MessageRole.USER,
            content=[ContentBlock(kind="image_url", image_url="data:...")],
        )
        assert msg.text() == ""


class TestTextMessage:
    def test_atalho_cria_bloco_unico_de_texto(self):
        msg = text_message(MessageRole.USER, "oi", name="agent")
        assert msg.role == MessageRole.USER
        assert msg.text() == "oi"
        assert msg.name == "agent"


class TestVMessageRoundTrip:
    def test_serializacao_preserva_tool_calls_e_blocos_multimodais(self):
        original = VMessage(
            role=MessageRole.ASSISTANT,
            content=[
                ContentBlock(kind="text", text="Vou checar."),
                ContentBlock(kind="reasoning", reasoning_text="pensando..."),
            ],
            tool_calls=[ToolCall(id="call_1", name="file_read", args={"path": "a.py"})],
            name="coder",
            finish_reason="tool_calls",
        )

        restored = VMessage.from_dict(original.to_dict())

        assert restored.role == original.role
        assert restored.text() == "Vou checar."
        assert restored.content[1].reasoning_text == "pensando..."
        assert restored.tool_calls == original.tool_calls
        assert restored.name == "coder"
        assert restored.finish_reason == "tool_calls"

    def test_round_trip_de_tool_result_preserva_is_error(self):
        """Erro/borda: resultado de tool marcado como erro precisa
        sobreviver ao round-trip — é o campo que popula `is_error` no
        evento SSE `tool_result`."""
        original = VMessage(
            role=MessageRole.TOOL,
            content=[ContentBlock(kind="text", text="Error: file not found")],
            tool_call_id="call_1",
            is_error=True,
        )

        restored = VMessage.from_dict(original.to_dict())

        assert restored.tool_call_id == "call_1"
        assert restored.is_error is True

    def test_from_dict_com_campos_ausentes_usa_defaults(self):
        """Erro/borda: dict mínimo (só `role`) não deve levantar KeyError —
        histórico persistido antes de um campo novo existir precisa
        continuar carregável."""
        restored = VMessage.from_dict({"role": "user"})

        assert restored.role == MessageRole.USER
        assert restored.content == []
        assert restored.tool_calls == []
        assert restored.is_error is False


class TestVMessageChunk:
    def test_chunk_default_sem_tool_calls_nem_usage(self):
        chunk = VMessageChunk(delta_text="oi")
        assert chunk.delta_text == "oi"
        assert chunk.tool_call_chunks == []
        assert chunk.usage is None

    def test_tool_call_chunk_carrega_indice_e_fragmento(self):
        chunk = VMessageChunk(
            tool_call_chunks=[
                ToolCallChunk(
                    index=0, id="call_1", name="file_read", args_fragment='{"path"'
                ),
            ]
        )
        assert chunk.tool_call_chunks[0].index == 0
        assert chunk.tool_call_chunks[0].args_fragment == '{"path"'
