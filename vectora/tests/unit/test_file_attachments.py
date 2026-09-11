"""File Handling Completo — testes.

Cobre:
- schemas Pydantic (Attachment, StreamChatRequest.attachments)
- _build_user_vmessage — conversão de attachments para VMessage multimodal
- _mime_to_lang — detecção de linguagem por extensão de arquivo
"""

from __future__ import annotations

import base64
from contextlib import ExitStack
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from backend.api.schemas import (
    Attachment,
    AttachmentKind,
    ChatConfig,
    StreamChatRequest,
)
from backend.settings import CapabilityState

# ===========================================================================
# Helpers
# ===========================================================================


def _b64(text: str) -> str:
    """Encoda texto para base64 (mesmo formato que o frontend envia)."""
    return base64.b64encode(text.encode()).decode()


def _b64_bytes(data: bytes) -> str:
    return base64.b64encode(data).decode()


# ===========================================================================
# Classe 1 — Schema de Attachment
# ===========================================================================


class TestAttachmentSchema:
    def test_image_attachment_valid(self) -> None:
        att = Attachment(
            kind=AttachmentKind.IMAGE,
            name="photo.png",
            mime_type="image/png",
            base64_data=_b64_bytes(b"\x89PNG\r\n\x1a\n"),
        )
        assert att.kind == AttachmentKind.IMAGE
        assert att.name == "photo.png"

    def test_code_attachment_valid(self) -> None:
        att = Attachment(
            kind=AttachmentKind.CODE,
            name="main.py",
            mime_type="text/x-python",
            base64_data=_b64("print('oi')"),
        )
        assert att.kind == AttachmentKind.CODE

    def test_pdf_attachment_valid(self) -> None:
        att = Attachment(
            kind=AttachmentKind.PDF,
            name="doc.pdf",
            mime_type="application/pdf",
            base64_data=_b64("%PDF-1.4"),
        )
        assert att.kind == AttachmentKind.PDF

    def test_text_attachment_valid(self) -> None:
        att = Attachment(
            kind=AttachmentKind.TEXT,
            name="readme.txt",
            mime_type="text/plain",
            base64_data=_b64("olá mundo"),
        )
        assert att.kind == AttachmentKind.TEXT

    def test_invalid_kind_raises(self) -> None:
        with pytest.raises(ValidationError):
            Attachment(
                kind=cast("AttachmentKind", "video"),  # tipo inválido
                name="clip.mp4",
                mime_type="video/mp4",
                base64_data="abc",
            )


# ===========================================================================
# Classe 1b — Validação de conteúdo (tamanho, mimetype, base64) — regra 8
# CLAUDE.md: o filtro do frontend (validation.ts) é só UX, a fonte de
# verdade real é essa validação server-side.
# ===========================================================================


class TestAttachmentContentValidation:
    def test_base64_within_default_limit_validates_above_limit_rejects(self) -> None:
        ok = Attachment(
            kind=AttachmentKind.CODE,
            name="script.py",
            mime_type="text/x-python",
            base64_data=_b64_bytes(b"x" * 1024),
        )
        assert ok.name == "script.py"

        too_big = _b64_bytes(b"x" * (10 * 1024 * 1024 + 1))
        with pytest.raises(ValidationError, match="excede o limite"):
            Attachment(
                kind=AttachmentKind.CODE,
                name="script.py",
                mime_type="text/x-python",
                base64_data=too_big,
            )

    def test_audio_uses_25mb_tier_instead_of_default_10mb(self) -> None:
        ok = Attachment(
            kind=AttachmentKind.AUDIO,
            name="nota.mp3",
            mime_type="audio/mpeg",
            base64_data=_b64_bytes(b"x" * (12 * 1024 * 1024)),
        )
        assert ok.kind == AttachmentKind.AUDIO

        too_big = _b64_bytes(b"x" * (25 * 1024 * 1024 + 1))
        with pytest.raises(ValidationError, match="excede o limite"):
            Attachment(
                kind=AttachmentKind.AUDIO,
                name="nota.mp3",
                mime_type="audio/mpeg",
                base64_data=too_big,
            )

    def test_har_uses_50mb_tier_by_extension_regardless_of_kind(self) -> None:
        ok = Attachment(
            kind=AttachmentKind.CODE,
            name="capture.har",
            mime_type="application/json",
            base64_data=_b64_bytes(b"x" * (30 * 1024 * 1024)),
        )
        assert ok.name == "capture.har"

        too_big = _b64_bytes(b"x" * (50 * 1024 * 1024 + 1))
        with pytest.raises(ValidationError, match="excede o limite"):
            Attachment(
                kind=AttachmentKind.CODE,
                name="capture.har",
                mime_type="application/json",
                base64_data=too_big,
            )

    def test_unsupported_type_rejected_extension_fallback_accepts(self) -> None:
        with pytest.raises(ValidationError, match="não suportado"):
            Attachment(
                kind=AttachmentKind.CODE,
                name="malware.exe",
                mime_type="application/x-msdownload",
                base64_data=_b64("conteudo"),
            )

        # mimetype vazio/genérico, mas extensão reconhecida — mesmo fallback
        # do frontend (alguns navegadores não reportam mimetype pra certas
        # extensões de texto/config).
        ok = Attachment(
            kind=AttachmentKind.CODE,
            name="config.yaml",
            mime_type="",
            base64_data=_b64("key: value"),
        )
        assert ok.name == "config.yaml"

    def test_invalid_base64_rejected(self) -> None:
        with pytest.raises(ValidationError, match="base64_data inválido"):
            Attachment(
                kind=AttachmentKind.CODE,
                name="script.py",
                mime_type="text/x-python",
                base64_data="not-valid-base64!!!",
            )


# ===========================================================================
# Classe 2 — StreamChatRequest com attachments
# ===========================================================================


class TestStreamChatRequestAttachments:
    def test_default_empty_attachments(self) -> None:
        req = StreamChatRequest(content="olá")
        assert req.attachments == []

    def test_request_with_single_attachment(self) -> None:
        att = Attachment(
            kind=AttachmentKind.IMAGE,
            name="img.png",
            mime_type="image/png",
            base64_data=_b64_bytes(b"\x89PNG\r\n\x1a\n"),
        )
        req = StreamChatRequest(content="veja isso", attachments=[att])
        assert len(req.attachments) == 1
        assert req.attachments[0].kind == AttachmentKind.IMAGE

    def test_request_json_roundtrip(self) -> None:
        att = Attachment(
            kind=AttachmentKind.CODE,
            name="main.py",
            mime_type="text/x-python",
            base64_data=_b64("print('hi')"),
        )
        req = StreamChatRequest(content="explique", attachments=[att])
        as_dict = req.model_dump()
        req2 = StreamChatRequest.model_validate(as_dict)
        assert req2.attachments[0].name == "main.py"
        assert req2.attachments[0].kind == AttachmentKind.CODE


# ===========================================================================
# Classe 3 — _build_user_vmessage
# ===========================================================================


class TestBuildUserVMessage:
    @pytest.fixture(autouse=True)
    def _isolated_vectora_home(self, tmp_path, monkeypatch):
        """``_build_user_vmessage`` com imagem grava em ``settings.vectora_home``
        (``_persist_image_attachment``) — nunca aponta pro ``~/.vectora`` real
        do ambiente rodando o teste."""
        from backend.settings import settings

        monkeypatch.setattr(settings, "vectora_home", tmp_path)

    @pytest.mark.asyncio
    async def test_no_attachments_returns_single_text_block(self) -> None:
        from backend.api.handlers.chat import _build_user_vmessage

        msg = await _build_user_vmessage("olá", [], "t1")
        assert len(msg.content) == 1
        assert msg.content[0].kind == "text"
        assert msg.content[0].text == "olá"

    @pytest.mark.asyncio
    async def test_empty_list_returns_single_text_block(self) -> None:
        from backend.api.handlers.chat import _build_user_vmessage

        msg = await _build_user_vmessage("teste", [], "t1")
        assert len(msg.content) == 1
        assert msg.content[0].kind == "text"
        assert msg.content[0].text == "teste"

    @pytest.mark.asyncio
    async def test_image_attachment_produces_multimodal(self) -> None:
        from backend.api.handlers.chat import _build_user_vmessage

        raw = _b64_bytes(b"fake_image_bytes")
        att = Attachment(
            kind=AttachmentKind.IMAGE,
            name="img.png",
            mime_type="image/png",
            base64_data=raw,
        )
        msg = await _build_user_vmessage("veja isso", [att], "t1")

        assert len(msg.content) == 2  # text + image
        assert msg.content[0].kind == "text"
        assert msg.content[0].text == "veja isso"
        assert msg.content[1].kind == "image_url"
        assert msg.content[1].image_url is not None
        assert "data:image/png;base64," in msg.content[1].image_url
        assert raw in msg.content[1].image_url

    @pytest.mark.asyncio
    async def test_image_attachment_e_persistida_em_disco_pra_sobreviver_a_restart(
        self, tmp_path
    ) -> None:
        """A imagem é persistida em disco (via ``_persist_image_attachment``)
        pra sobreviver a um restart do backend — o VMessage não carrega
        metadados de anexo (o ``additional_kwargs`` do antigo ``BaseMessage`` foi
        removido), mas o arquivo continua salvo em ``chat-attachments/``."""
        from backend.api.handlers.chat import _build_user_vmessage

        raw_bytes = b"\x89PNG\r\n\x1a\nfake-image-content"
        att = Attachment(
            kind=AttachmentKind.IMAGE,
            name="screenshot.png",
            mime_type="image/png",
            base64_data=_b64_bytes(raw_bytes),
        )
        await _build_user_vmessage("veja isso", [att], "thread-xyz")

        # A imagem foi persistida em disco.
        attachments_dir = tmp_path / "chat-attachments" / "thread-xyz"
        persisted = list(attachments_dir.glob("*"))
        assert len(persisted) == 1
        assert persisted[0].is_file()
        assert persisted[0].read_bytes() == raw_bytes

    @pytest.mark.asyncio
    async def test_falha_ao_criar_asset_remove_arquivo_persistido(self, monkeypatch):
        from backend.api.handlers.chat import _build_user_vmessage
        from backend.services.assets import asset_store

        def fail_create(**_kwargs):
            raise ValueError("asset inválido")

        monkeypatch.setattr(asset_store, "create", fail_create)
        att = Attachment(
            kind=AttachmentKind.IMAGE,
            name="invalid.png",
            mime_type="image/png",
            base64_data=_b64_bytes(b"image"),
        )
        msg = await _build_user_vmessage("veja", [att], "thread-invalid")

        assert msg.content[1].asset_id is None
        assert msg.content[1].attachment_name is None
        from backend.settings import settings

        assert not [
            path
            for path in (settings.vectora_home / "chat-attachments").rglob("*")
            if path.is_file()
        ]

    @pytest.mark.asyncio
    async def test_mime_invalido_nao_grava_arquivo(self, monkeypatch):
        from backend.api.handlers.chat import _persist_image_file

        att = Attachment(
            kind=AttachmentKind.IMAGE,
            name="invalid.png",
            mime_type="image/png",
            base64_data=_b64_bytes(b"image"),
        )
        from backend.services import assets

        monkeypatch.setattr(assets, "ALLOWED_MIME", {"audio/mpeg"})
        assert _persist_image_file("thread-invalid", att) is None

    @pytest.mark.asyncio
    async def test_falha_ao_persistir_nao_aborta_o_turno(self, monkeypatch) -> None:
        """Erro/borda: disco cheio/sem permissão não pode derrubar o chat —
        a imagem já foi enviada ao provider via base64 inline, só a
        reexibição pós-restart fica indisponível. Testa
        ``_persist_image_attachment`` isolado (não via ``_build_user_vmessage``)
        pra exercitar o ``try/except`` real da função, não um mock que o
        contorna."""
        from pathlib import Path as PathCls

        from backend.api.handlers.chat import _persist_image_attachment

        def _boom(self, *_a: object, **_kw: object) -> None:
            raise OSError("disco cheio")

        monkeypatch.setattr(PathCls, "mkdir", _boom)

        att = Attachment(
            kind=AttachmentKind.IMAGE,
            name="img.png",
            mime_type="image/png",
            base64_data=_b64_bytes(b"bytes"),
        )

        url = _persist_image_attachment("t1", att)

        assert url is None

    @pytest.mark.asyncio
    async def test_code_attachment_injects_code_block(self) -> None:
        from backend.api.handlers.chat import _build_user_vmessage

        code = "def hello():\n    print('hi')"
        att = Attachment(
            kind=AttachmentKind.CODE,
            name="hello.py",
            mime_type="text/x-python",
            base64_data=_b64(code),
        )
        msg = await _build_user_vmessage("explique", [att], "t1")

        all_text = "\n".join(b.text or "" for b in msg.content if b.kind == "text")
        assert "hello.py" in all_text
        assert "python" in all_text  # linguagem detectada pela extensão
        assert code in all_text

    @pytest.mark.asyncio
    async def test_pdf_attachment_injects_decoded_text(self) -> None:
        from backend.api.handlers.chat import _build_user_vmessage

        content = "Este é o conteúdo do PDF"
        att = Attachment(
            kind=AttachmentKind.PDF,
            name="relatorio.pdf",
            mime_type="application/pdf",
            base64_data=_b64(content),
        )
        msg = await _build_user_vmessage("resuma", [att], "t1")

        all_text = "\n".join(b.text or "" for b in msg.content if b.kind == "text")
        assert "relatorio.pdf" in all_text
        assert content in all_text

    @pytest.mark.asyncio
    async def test_text_attachment_decoded_utf8(self) -> None:
        from backend.api.handlers.chat import _build_user_vmessage

        content = "Olá, Mundo! \U0001f30d"
        att = Attachment(
            kind=AttachmentKind.TEXT,
            name="nota.txt",
            mime_type="text/plain",
            base64_data=_b64(content),
        )
        msg = await _build_user_vmessage("leia", [att], "t1")

        all_text = "\n".join(b.text or "" for b in msg.content if b.kind == "text")
        assert content in all_text

    @pytest.mark.asyncio
    async def test_mixed_attachments_correct_parts_count(self) -> None:
        from backend.api.handlers.chat import _build_user_vmessage

        img_att = Attachment(
            kind=AttachmentKind.IMAGE,
            name="img.png",
            mime_type="image/png",
            base64_data=_b64_bytes(b"img"),
        )
        code_att = Attachment(
            kind=AttachmentKind.CODE,
            name="script.py",
            mime_type="text/x-python",
            base64_data=_b64("x = 1"),
        )
        msg = await _build_user_vmessage("analyze", [img_att, code_att], "t1")

        assert len(msg.content) == 3  # texto + image_url + texto-código
        kinds = [b.kind for b in msg.content]
        assert kinds == ["text", "image_url", "text"]

    @pytest.mark.asyncio
    async def test_multiple_images(self) -> None:
        from backend.api.handlers.chat import _build_user_vmessage

        att1 = Attachment(
            kind=AttachmentKind.IMAGE,
            name="a.png",
            mime_type="image/png",
            base64_data=_b64_bytes(b"img1"),
        )
        att2 = Attachment(
            kind=AttachmentKind.IMAGE,
            name="b.jpg",
            mime_type="image/jpeg",
            base64_data=_b64_bytes(b"img2"),
        )
        msg = await _build_user_vmessage("compare", [att1, att2], "t1")

        assert len(msg.content) == 3  # text + 2 images
        assert msg.content[1].kind == "image_url"
        assert msg.content[2].kind == "image_url"
        assert msg.content[2].image_url is not None
        assert "image/jpeg" in msg.content[2].image_url

    @pytest.mark.asyncio
    async def test_audio_attachment_transcribed_and_injected(self) -> None:
        from unittest.mock import AsyncMock, patch

        from backend.api.handlers.chat import _build_user_vmessage

        att = Attachment(
            kind=AttachmentKind.AUDIO,
            name="memo.mp3",
            mime_type="audio/mpeg",
            base64_data=_b64_bytes(b"fake_audio_bytes"),
        )
        with patch(
            "backend.llm.transcription.transcribe_audio",
            AsyncMock(return_value="fale sobre o projeto"),
        ):
            msg = await _build_user_vmessage("ouça isso", [att], "t1")

        all_text = "\n".join(b.text or "" for b in msg.content if b.kind == "text")
        assert "memo.mp3" in all_text
        assert "fale sobre o projeto" in all_text

    @pytest.mark.asyncio
    async def test_audio_attachment_transcription_failure_injects_error_note(
        self,
    ) -> None:
        from unittest.mock import AsyncMock, patch

        from backend.api.handlers.chat import _build_user_vmessage
        from backend.llm.transcription import TranscriptionError

        att = Attachment(
            kind=AttachmentKind.AUDIO,
            name="memo.wav",
            mime_type="audio/wav",
            base64_data=_b64_bytes(b"fake_audio_bytes"),
        )
        with patch(
            "backend.llm.transcription.transcribe_audio",
            AsyncMock(side_effect=TranscriptionError("sem chave configurada")),
        ):
            msg = await _build_user_vmessage("ouça isso", [att], "t1")

        all_text = "\n".join(b.text or "" for b in msg.content if b.kind == "text")
        assert "memo.wav" in all_text
        assert "falha ao transcrever" in all_text


# Classe 4 — _mime_to_lang
# ===========================================================================


class TestMimeToLang:
    def test_python_by_extension(self) -> None:
        from backend.api.handlers.chat import _mime_to_lang

        assert _mime_to_lang("script.py") == "python"

    def test_typescript_by_extension(self) -> None:
        from backend.api.handlers.chat import _mime_to_lang

        assert _mime_to_lang("app.ts") == "typescript"

    def test_tsx_by_extension(self) -> None:
        from backend.api.handlers.chat import _mime_to_lang

        assert _mime_to_lang("component.tsx") == "typescript"

    def test_json_by_extension(self) -> None:
        from backend.api.handlers.chat import _mime_to_lang

        assert _mime_to_lang("config.json") == "json"

    def test_pdf_returns_empty_string(self) -> None:
        from backend.api.handlers.chat import _mime_to_lang

        assert _mime_to_lang("doc.pdf") == ""

    def test_unknown_extension_returns_empty(self) -> None:
        from backend.api.handlers.chat import _mime_to_lang

        assert _mime_to_lang("file.xyz") == ""

    def test_shell_script(self) -> None:
        from backend.api.handlers.chat import _mime_to_lang

        assert _mime_to_lang("run.sh") == "bash"


# ===========================================================================
# Classe 5 — stream_chat recusa imagem quando o provider não suporta visão
# ===========================================================================


def _image_attachment() -> Attachment:
    return Attachment(
        kind=AttachmentKind.IMAGE,
        name="foto.png",
        mime_type="image/png",
        base64_data=_b64("fake-image-bytes"),
    )


async def _collect_sse_body(response) -> str:
    chunks = [chunk async for chunk in response.body_iterator]
    return "".join(
        chunk.decode() if isinstance(chunk, bytes) else chunk for chunk in chunks
    )


def _native_dispatch_patches() -> list[Any]:
    """Patches pro motor nativo (`agent_factory.get_native_agent`/
    `get_session_store`/`get_approval_gate`/`get_store` + `run_conversation`
    dentro de `backend.api.handlers.chat`) — usados pelos testes que
    precisam que `stream_chat` alcance o dispatch de verdade (fluxo "não
    bloqueado") sem rodar o loop de conversa real."""
    from backend.engine.conversation_loop import LoopResult
    from backend.services.agent_factory import NativeAgent
    from backend.tools.registry import ToolRegistry

    fake_agent = NativeAgent(
        tool_registry=ToolRegistry(), subagent_catalog={}, system_prompt="prompt"
    )
    session_store = AsyncMock()
    session_store.create_session = AsyncMock()
    session_store.get_branch_head_id = AsyncMock(return_value=1)
    session_store.append_message = AsyncMock(return_value=2)
    session_store.set_branch_head = AsyncMock()

    return [
        patch(
            "backend.services.agent_factory.get_native_agent",
            new=AsyncMock(return_value=fake_agent),
        ),
        patch(
            "backend.services.agent_factory.get_session_store",
            new=AsyncMock(return_value=session_store),
        ),
        patch(
            "backend.services.agent_factory.get_approval_gate",
            new=AsyncMock(return_value=AsyncMock()),
        ),
        patch(
            "backend.services.agent_factory.get_store",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "backend.api.handlers.chat.run_conversation",
            new=AsyncMock(return_value=LoopResult(stopped_reason="stop")),
        ),
    ]


class TestStreamChatBlocksImageForNonVisionProvider:
    """Regressão: imagem + Cohere estourava BadRequestError cru da API
    ('image content is not supported for this model'). Agora recusa antes
    de chamar o provider, com um ErrorEvent(code='MODEL_NO_VISION')."""

    @pytest.fixture(autouse=True)
    def _isolated_vectora_home(self, tmp_path, monkeypatch):
        """`stream_chat` com anexo de imagem grava em `settings.vectora_home`
        (`_persist_image_attachment`) — nunca aponta pro `~/.vectora` real
        do ambiente rodando o teste."""
        from backend.settings import settings

        monkeypatch.setattr(settings, "vectora_home", tmp_path)

    @pytest.mark.asyncio
    async def test_cohere_with_image_returns_model_no_vision_without_calling_provider(
        self,
    ) -> None:
        from backend.api.handlers import chat as chat_mod

        mock_get_native_agent = AsyncMock()
        with patch(
            "backend.services.agent_factory.get_native_agent", mock_get_native_agent
        ):
            request = StreamChatRequest(
                content="o que tem nessa imagem?",
                config=ChatConfig(model="cohere:command-a-03-2025"),
                attachments=[_image_attachment()],
            )
            http_request = MagicMock()
            http_request.state = MagicMock(user=None)
            http_request.is_disconnected = AsyncMock(return_value=False)
            response = await chat_mod.stream_chat(request, http_request)

        body = await _collect_sse_body(response)
        assert '"code": "MODEL_NO_VISION"' in body
        mock_get_native_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_gemini_with_image_is_not_blocked(self) -> None:
        """Provider com suporte a visão (google_genai) não é bloqueado —
        confirma que a checagem é por provider, não um bloqueio geral."""
        from backend.api.handlers import chat as chat_mod

        with ExitStack() as stack:
            for p in _native_dispatch_patches():
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "backend.api.handlers.threads._upsert_session",
                    new=AsyncMock(),
                )
            )
            request = StreamChatRequest(
                content="o que tem nessa imagem?",
                config=ChatConfig(model="google-genai:gemini-2.5-flash"),
                attachments=[_image_attachment()],
            )
            http_request = MagicMock()
            http_request.state = MagicMock(user=None)
            response = await chat_mod.stream_chat(request, http_request)
            body = await _collect_sse_body(response)

        assert "MODEL_NO_VISION" not in body

    @pytest.mark.asyncio
    async def test_openrouter_model_without_vision_is_blocked(self) -> None:
        """Modelo OpenRouter real sem `input_modalities: [..., "image"]` no
        catálogo é bloqueado — igual a um provider direto sem visão."""
        from backend.api.handlers import chat as chat_mod

        mock_get_native_agent = AsyncMock()
        with (
            patch(
                "backend.services.agent_factory.get_native_agent", mock_get_native_agent
            ),
            patch(
                "backend.api.handlers.provider_routing.openrouter_model_image_state",
                new=AsyncMock(return_value=CapabilityState.UNSUPPORTED),
            ),
        ):
            request = StreamChatRequest(
                content="o que tem nessa imagem?",
                config=ChatConfig(model="openrouter:deepseek/deepseek-r1"),
                attachments=[_image_attachment()],
            )
            http_request = MagicMock()
            http_request.state = MagicMock(user=None)
            http_request.is_disconnected = AsyncMock(return_value=False)
            response = await chat_mod.stream_chat(request, http_request)

        body = await _collect_sse_body(response)
        assert '"code": "MODEL_NO_VISION"' in body
        mock_get_native_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_openrouter_model_with_vision_is_not_blocked(self) -> None:
        """Erro/borda central do bug original: nem todo modelo servido via
        OpenRouter é igual — um com `input_modalities` incluindo "image" no
        catálogo não pode ser bloqueado só por o provider ser "openrouter"."""
        from backend.api.handlers import chat as chat_mod

        with ExitStack() as stack:
            for p in _native_dispatch_patches():
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "backend.api.handlers.threads._upsert_session",
                    new=AsyncMock(),
                )
            )
            stack.enter_context(
                patch(
                    "backend.api.handlers.provider_routing.openrouter_model_image_state",
                    new=AsyncMock(return_value=CapabilityState.SUPPORTED),
                )
            )
            request = StreamChatRequest(
                content="o que tem nessa imagem?",
                config=ChatConfig(model="openrouter:openai/gpt-4o"),
                attachments=[_image_attachment()],
            )
            http_request = MagicMock()
            http_request.state = MagicMock(user=None)
            response = await chat_mod.stream_chat(request, http_request)
            body = await _collect_sse_body(response)

        assert "MODEL_NO_VISION" not in body

    @pytest.mark.asyncio
    async def test_openrouter_model_with_unknown_capability_is_not_blocked(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Catálogo indisponível não deve bloquear o envio preventivamente."""
        from backend.api.handlers import chat as chat_mod

        native_agent = None
        with ExitStack() as stack:
            for p in _native_dispatch_patches():
                entered = stack.enter_context(p)
                if p.attribute == "get_native_agent":
                    native_agent = entered
            stack.enter_context(
                patch(
                    "backend.api.handlers.provider_routing.openrouter_model_image_state",
                    new=AsyncMock(return_value=CapabilityState.UNKNOWN),
                )
            )
            request = StreamChatRequest(
                content="o que tem nessa imagem?",
                config=ChatConfig(model="openrouter:openai/gpt-4o"),
                attachments=[_image_attachment()],
            )
            http_request = MagicMock()
            http_request.state = MagicMock(user=None)
            http_request.is_disconnected = AsyncMock(return_value=False)
            response = await chat_mod.stream_chat(request, http_request)

        body = await _collect_sse_body(response)
        assert '"code": "MODEL_NO_VISION"' not in body
        assert native_agent is not None
        native_agent.assert_called_once()

    @pytest.mark.asyncio
    async def test_ignora_fallback_persistido_apos_remover_credencial(
        self, monkeypatch
    ) -> None:
        from backend.api.handlers import chat as chat_mod
        from backend.settings import CapabilityState

        monkeypatch.setattr(
            "backend.workspace.runtime_settings.runtime_settings.get",
            lambda key: "openai:gpt-4o" if key == "image_fallback_model" else None,
        )
        monkeypatch.setattr(
            "backend.llm.provider_fallback._provider_has_key",
            lambda _provider: False,
        )
        monkeypatch.setattr(
            chat_mod,
            "_model_supports_vision",
            AsyncMock(return_value=CapabilityState.SUPPORTED),
        )

        assert await chat_mod._resolve_image_fallback_model() is None

    @pytest.mark.asyncio
    async def test_ignora_fallback_persistido_sem_modelo(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from backend.api.handlers import chat as chat_mod
        from backend.settings import CapabilityState

        monkeypatch.setattr(
            "backend.workspace.runtime_settings.runtime_settings.get",
            lambda key, default=None: (
                "openai:" if key == "image_fallback_model" else default
            ),
        )
        monkeypatch.setattr(
            "backend.llm.provider_fallback._provider_has_key",
            lambda _provider: True,
        )
        monkeypatch.setattr(
            chat_mod,
            "_model_supports_vision",
            AsyncMock(return_value=CapabilityState.SUPPORTED),
        )

        assert await chat_mod._resolve_image_fallback_model() is None


class TestStreamChatBlocksToolIncompatibleModelInCodeMode:
    """Command A+ (cohere:command-a-plus-05-2026) rejeita replay de tool_calls
    no histórico — code mode sempre usa tools (ALL_TOOLS), então o modelo
    nunca funciona lá. Chat mode não é bloqueado (decisão de produto)."""

    @pytest.mark.asyncio
    async def test_code_mode_returns_model_no_tool_calling_without_calling_provider(
        self,
    ) -> None:
        from backend.api.handlers import chat as chat_mod

        mock_get_native_agent = AsyncMock()
        with patch(
            "backend.services.agent_factory.get_native_agent", mock_get_native_agent
        ):
            request = StreamChatRequest(
                content="cria um jogo da cobrinha em godot",
                config=ChatConfig(
                    model="cohere:command-a-plus-05-2026", chat_mode=False
                ),
            )
            http_request = MagicMock()
            http_request.state = MagicMock(user=None)
            response = await chat_mod.stream_chat(request, http_request)

        body = await _collect_sse_body(response)
        assert '"code": "MODEL_NO_TOOL_CALLING"' in body
        mock_get_native_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_chat_mode_is_not_blocked(self) -> None:
        """Mesmo modelo, chat_mode=True — não é bloqueado (o risco é aceito
        pelo produto no modo conversacional)."""
        from backend.api.handlers import chat as chat_mod

        with ExitStack() as stack:
            for p in _native_dispatch_patches():
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "backend.api.handlers.threads._upsert_session",
                    new=AsyncMock(),
                )
            )
            request = StreamChatRequest(
                content="oi",
                config=ChatConfig(
                    model="cohere:command-a-plus-05-2026", chat_mode=True
                ),
            )
            http_request = MagicMock()
            http_request.state = MagicMock(user=None)
            response = await chat_mod.stream_chat(request, http_request)
            body = await _collect_sse_body(response)

        assert "MODEL_NO_TOOL_CALLING" not in body


class TestStreamChatEnforcesAllowedModels:
    """`[agent].allowed_models` do vectora.toml do workspace é a fonte de
    verdade real — antes disso a única barreira era o filtro client-side do
    seletor de modelo (deployment-config.ts), sem checagem server-side."""

    @pytest.mark.asyncio
    async def test_model_outside_allowed_models_is_rejected_without_calling_provider(
        self,
    ) -> None:
        from backend.api.handlers import chat as chat_mod
        from backend.workspace.workspace_config import AgentSection, WorkspaceConfig

        fake_ws = MagicMock(cwd="/fake/workspace")
        mock_get_native_agent = AsyncMock()
        with (
            patch(
                "backend.workspace.workspace.workspace_registry.get",
                return_value=fake_ws,
            ),
            patch(
                "backend.workspace.workspace_config.load_workspace_config",
                return_value=WorkspaceConfig(
                    agent=AgentSection(allowed_models=["anthropic:claude-sonnet-4-6"])
                ),
            ),
            patch(
                "backend.services.agent_factory.get_native_agent", mock_get_native_agent
            ),
        ):
            request = StreamChatRequest(
                content="oi",
                config=ChatConfig(
                    model="google-genai:gemini-3-flash-preview",
                    chat_mode=False,
                    workspace_id="/fake/workspace",
                ),
            )
            http_request = MagicMock()
            http_request.state = MagicMock(user=None)
            response = await chat_mod.stream_chat(request, http_request)

        body = await _collect_sse_body(response)
        assert '"code": "MODEL_NOT_ALLOWED"' in body
        mock_get_native_agent.assert_not_called()

    @pytest.mark.asyncio
    async def test_model_inside_allowed_models_is_accepted(self) -> None:
        from backend.api.handlers import chat as chat_mod
        from backend.workspace.workspace_config import AgentSection, WorkspaceConfig

        fake_ws = MagicMock(cwd="/fake/workspace")

        with ExitStack() as stack:
            for p in _native_dispatch_patches():
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "backend.workspace.workspace.workspace_registry.get",
                    return_value=fake_ws,
                )
            )
            stack.enter_context(
                patch(
                    "backend.workspace.workspace_config.load_workspace_config",
                    return_value=WorkspaceConfig(
                        agent=AgentSection(
                            allowed_models=["google-genai:gemini-3-flash-preview"]
                        )
                    ),
                )
            )
            stack.enter_context(
                patch(
                    "backend.api.handlers.threads._upsert_session",
                    new=AsyncMock(),
                )
            )
            request = StreamChatRequest(
                content="oi",
                config=ChatConfig(
                    model="google-genai:gemini-3-flash-preview",
                    chat_mode=False,
                    workspace_id="/fake/workspace",
                ),
            )
            http_request = MagicMock()
            http_request.state = MagicMock(user=None)
            response = await chat_mod.stream_chat(request, http_request)
            body = await _collect_sse_body(response)

        assert "MODEL_NOT_ALLOWED" not in body

    @pytest.mark.asyncio
    async def test_workspace_without_allowed_models_accepts_any_model(self) -> None:
        """Sem `vectora.toml` (ou sem `allowed_models` nele), comportamento
        atual é preservado — nenhum modelo é bloqueado por essa checagem."""
        from backend.api.handlers import chat as chat_mod

        fake_ws = MagicMock(cwd="/fake/workspace")

        with ExitStack() as stack:
            for p in _native_dispatch_patches():
                stack.enter_context(p)
            stack.enter_context(
                patch(
                    "backend.workspace.workspace.workspace_registry.get",
                    return_value=fake_ws,
                )
            )
            stack.enter_context(
                patch(
                    "backend.workspace.workspace_config.load_workspace_config",
                    return_value=None,
                )
            )
            stack.enter_context(
                patch(
                    "backend.api.handlers.threads._upsert_session",
                    new=AsyncMock(),
                )
            )
            request = StreamChatRequest(
                content="oi",
                config=ChatConfig(
                    model="qualquer:modelo-nao-listado",
                    chat_mode=False,
                    workspace_id="/fake/workspace",
                ),
            )
            http_request = MagicMock()
            http_request.state = MagicMock(user=None)
            response = await chat_mod.stream_chat(request, http_request)
            body = await _collect_sse_body(response)

        assert "MODEL_NOT_ALLOWED" not in body
