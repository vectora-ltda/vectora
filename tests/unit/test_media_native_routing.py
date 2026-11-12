"""Envio nativo de anexos por provider, com fallback para texto."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from backend.llm.media_parts import (
    downgrade_to_text,
    media_routing_evidence,
    prepare_content,
)
from backend.services.media_hydration import hydrate_media_blocks
from backend.vtypes.message import ContentBlock, MessageRole, VMessage, VMessageChunk

PDF = b"%PDF-1.7\nconteudo"
PDF_URL = "data:application/pdf;base64," + base64.b64encode(PDF).decode()


def _pdf_block(*, data_url: str | None = PDF_URL, size: int = len(PDF)) -> ContentBlock:
    return ContentBlock(
        kind="media",
        media_kind="document",
        text="[Documento: r.pdf]\ntexto extraído",
        asset_id="asset-pdf",
        attachment_name="r.pdf",
        attachment_mime_type="application/pdf",
        attachment_size_bytes=size,
        media_data_url=data_url,
    )


def _user(*blocks: ContentBlock) -> VMessage:
    return VMessage(
        role=MessageRole.USER,
        content=[ContentBlock(kind="text", text="resuma"), *blocks],
    )


class TestPrepareContent:
    def test_pdf_nativo_para_anthropic_gemini_e_openai(self) -> None:
        for provider in ("anthropic", "google-genai", "google_genai", "openai"):
            prepared = prepare_content(_user(_pdf_block()).content, provider)
            assert prepared[1].kind == "media", provider

    def test_gateway_e_local_recebem_texto(self) -> None:
        for provider in ("openrouter", "ollama", "cohere", "llamacpp"):
            prepared = prepare_content(_user(_pdf_block()).content, provider)
            assert prepared[1] == ContentBlock(
                kind="text", text="[Documento: r.pdf]\ntexto extraído"
            )

    def test_sem_arquivo_carregado_ou_acima_do_limite_vira_texto(self) -> None:
        assert (
            prepare_content(_user(_pdf_block(data_url=None)).content, "anthropic")[
                1
            ].kind
            == "text"
        )
        huge = _pdf_block(size=40 * 1024 * 1024)
        assert prepare_content(_user(huge).content, "anthropic")[1].kind == "text"

    def test_audio_e_video_so_no_gemini(self) -> None:
        audio = ContentBlock(
            kind="media",
            media_kind="audio",
            text="transcrição",
            attachment_mime_type="audio/mpeg",
            attachment_size_bytes=10,
            media_data_url="data:audio/mpeg;base64,AAAA",
        )
        assert prepare_content([audio], "google_genai")[0].kind == "media"
        assert prepare_content([audio], "anthropic")[0].kind == "text"
        assert prepare_content([audio], "openai")[0].kind == "text"

    def test_evidencia_explica_o_modo(self) -> None:
        entries = media_routing_evidence(
            [_user(_pdf_block())], "openrouter", "openrouter:x"
        )
        assert entries[0]["mode"] == "text"
        assert entries[0]["sha256"] == ""
        native = media_routing_evidence(
            [_user(_pdf_block())], "anthropic", "anthropic:claude"
        )
        assert native[0]["mode"] == "native"

    def test_downgrade_remove_o_arquivo(self) -> None:
        [message] = downgrade_to_text([_user(_pdf_block())])
        assert message.content[1].media_data_url is None
        assert prepare_content(message.content, "anthropic")[1].kind == "text"


class TestProviderPayloads:
    def test_anthropic_document_block(self) -> None:
        from backend.llm.anthropic.chat_client import _to_anthropic_messages

        _system, messages = _to_anthropic_messages([_user(_pdf_block())])
        document = messages[0]["content"][1]
        assert document["type"] == "document"
        assert document["source"] == {
            "type": "base64",
            "media_type": "application/pdf",
            "data": base64.b64encode(PDF).decode(),
        }
        assert document["title"] == "r.pdf"

    def test_gemini_inline_data(self) -> None:
        from backend.llm.google.chat_client import _to_google_user_parts

        parts = _to_google_user_parts(_user(_pdf_block()).content)
        assert parts[1] == {
            "inlineData": {
                "mimeType": "application/pdf",
                "data": base64.b64encode(PDF).decode(),
            }
        }

    def test_openai_input_file(self) -> None:
        from backend.llm.openai.chat_client import _to_openai_input

        [item] = _to_openai_input([_user(_pdf_block())])
        assert item["content"][1] == {
            "type": "input_file",
            "filename": "r.pdf",
            "file_data": PDF_URL,
        }

    def test_openrouter_recebe_a_forma_textual(self) -> None:
        from backend.llm.openrouter.chat_client import _to_openrouter_message

        message = _to_openrouter_message(_user(_pdf_block()))
        assert message["content"] == "resuma[Documento: r.pdf]\ntexto extraído"

    def test_ollama_recebe_a_forma_textual(self) -> None:
        from backend.llm.ollama.chat_client import _split_conteudo

        text, images = _split_conteudo(_user(_pdf_block()))
        assert "texto extraído" in text
        assert images == []


class TestHydration:
    def test_so_a_mensagem_mais_recente_carrega_o_arquivo(self) -> None:
        old = _user(_pdf_block(data_url=None))
        latest = _user(_pdf_block(data_url=None))
        reads: list[str] = []

        def reader(asset_id: str, **_kwargs: object) -> tuple[object, bytes]:
            reads.append(asset_id)
            return object(), PDF

        sent = hydrate_media_blocks(
            [old, latest],
            user_id="u",
            workspace_id="",
            thread_id="t",
            reader=reader,
        )
        assert sent[0].content[1].media_data_url is None
        assert sent[1].content[1].media_data_url == PDF_URL
        assert reads == ["asset-pdf"]

    def test_tipo_sem_provider_nativo_nao_e_carregado(self) -> None:
        binary = ContentBlock(
            kind="media",
            media_kind="file",
            text="ref",
            asset_id="bin",
            attachment_mime_type="application/octet-stream",
            attachment_size_bytes=3,
        )

        def reader(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("não deveria ler")

        [message] = hydrate_media_blocks(
            [_user(binary)], user_id="u", workspace_id="", thread_id="t", reader=reader
        )
        assert message.content[1].media_data_url is None


class _Script:
    def __init__(self, *steps: object) -> None:
        self.steps = list(steps)
        self.received: list[list[VMessage]] = []

    async def agenerate(
        self, messages, *, tools=None, temperature=None, max_tokens=None
    ):
        raise NotImplementedError

    async def astream(self, messages, *, tools=None, temperature=None, max_tokens=None):
        self.received.append(list(messages))
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        yield VMessageChunk(delta_text=str(step))


async def _collect(client, messages):
    return [chunk async for chunk in client.astream(messages)]


async def test_recusa_do_arquivo_repete_com_texto_no_mesmo_modelo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.llm.fallback_chat_client import FallbackChatClient

    script = _Script(RuntimeError("400: document type not supported"), "ok")

    async def candidates(*_args: object, **_kwargs: object) -> list[str]:
        return ["anthropic:claude"]

    monkeypatch.setattr("backend.llm.fallback_chat_client._candidates", candidates)
    monkeypatch.setattr(
        "backend.llm.fallback_chat_client.load_chat_client", lambda _mid: script
    )
    client = FallbackChatClient("anthropic:claude")
    chunks = await _collect(client, [_user(_pdf_block())])

    assert [chunk.delta_text for chunk in chunks] == ["ok"]
    assert client.last_media_downgraded is True
    assert script.received[0][0].content[1].media_data_url == PDF_URL
    assert script.received[1][0].content[1].media_data_url is None


async def test_erro_sem_relacao_com_anexo_nao_repete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.llm.fallback_chat_client import FallbackChatClient

    script = _Script(ValueError("chave inválida"))

    async def candidates(*_args: object, **_kwargs: object) -> list[str]:
        return ["anthropic:claude"]

    monkeypatch.setattr("backend.llm.fallback_chat_client._candidates", candidates)
    monkeypatch.setattr(
        "backend.llm.fallback_chat_client.load_chat_client", lambda _mid: script
    )
    client = FallbackChatClient("anthropic:claude")
    with pytest.raises(ValueError, match="chave inválida"):
        await _collect(client, [_user(_pdf_block())])
    assert len(script.received) == 1


async def test_loop_registra_e_emite_o_envio_dos_anexos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.engine.conversation_loop import LoopConfig, run_conversation
    from backend.engine.stream_events import MediaRouted
    from backend.persistence.native.session_store import SessionStore
    from backend.storage.sqlite.pool import AsyncConnectionPool
    from backend.tools.context import ToolContext
    from backend.tools.registry import ToolRegistry

    pool = AsyncConnectionPool(str(tmp_path / "s.db"), min_size=1, max_size=2)
    await pool.open()
    store = SessionStore(pool)
    await store.setup()
    try:
        await store.create_session("t1", user_id="alice")
        await store.append_message("t1", _user(_pdf_block(data_url=None)))
        monkeypatch.setattr(
            "backend.services.media_hydration._default_reader",
            lambda: lambda *_a, **_k: (object(), PDF),
        )
        received: list[list[VMessage]] = []

        class _Client:
            last_model_id = "anthropic:claude"

            async def agenerate(self, messages, **_kwargs):
                raise NotImplementedError

            async def astream(
                self, messages, *, tools=None, temperature=None, max_tokens=None
            ):
                received.append(list(messages))
                yield VMessageChunk(delta_text="resumo")

        events: list[object] = []

        async def on_event(event: object) -> None:
            events.append(event)

        await run_conversation(
            session_store=store,
            chat_client=_Client(),
            tool_registry=ToolRegistry(),
            ctx=ToolContext(user_id="alice", thread_id="t1"),
            thread_id="t1",
            config=LoopConfig(),
            on_event=on_event,
        )
    finally:
        await pool.close()

    assert received[0][-1].content[1].media_data_url == PDF_URL
    [routed] = [event for event in events if isinstance(event, MediaRouted)]
    assert [(r.name, r.mode, r.model) for r in routed.routes] == [
        ("r.pdf", "native", "anthropic:claude")
    ]
    # O histórico persistido continua sem os bytes.
    assert all(
        block.media_data_url is None
        for message in await _history(tmp_path)
        for block in message.content
    )


async def _history(tmp_path: Path) -> list[VMessage]:
    from backend.persistence.native.session_store import SessionStore
    from backend.storage.sqlite.pool import AsyncConnectionPool

    pool = AsyncConnectionPool(str(tmp_path / "s.db"), min_size=1, max_size=2)
    await pool.open()
    try:
        return await SessionStore(pool).get_history("t1")
    finally:
        await pool.close()


def test_evento_sse_media_routed() -> None:
    import json

    from backend.engine.sse_adapter import to_sse_line
    from backend.engine.stream_events import MediaRoute, MediaRouted

    line = to_sse_line(
        MediaRouted(
            routes=[
                MediaRoute(
                    name="r.pdf",
                    media_kind="document",
                    mime_type="application/pdf",
                    size_bytes=3,
                    mode="native",
                    reason="provider aceita o tipo",
                    model="anthropic:claude",
                )
            ]
        )
    )
    data = json.loads(line.removeprefix("data: ").strip())
    assert data["type"] == "media_routed"
    assert data["routes"][0]["mode"] == "native"
