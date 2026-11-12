"""PDF, áudio e vídeo nativos em gateways, conforme o catálogo do modelo."""

from __future__ import annotations

import base64
from typing import cast

import pytest

from backend.llm.media_parts import (
    media_for_modalities,
    prepare_content,
    resolve_native_media,
)
from backend.llm.openrouter.client import OpenRouterClient
from backend.vtypes.message import (
    ContentBlock,
    MediaKind,
    MessageRole,
    VMessage,
    VMessageChunk,
)

PDF = b"%PDF-1.7\nconteudo"
PDF_URL = "data:application/pdf;base64," + base64.b64encode(PDF).decode()
MP3_URL = "data:audio/mpeg;base64,SUQz"


def _media(
    mime: str, url: str, *, kind: MediaKind = "document", size: int = 10
) -> ContentBlock:
    return ContentBlock(
        kind="media",
        media_kind=kind,
        text=f"[texto de {mime}]",
        asset_id="a1",
        attachment_name="anexo",
        attachment_mime_type=mime,
        attachment_size_bytes=size,
        media_data_url=url,
    )


def _user(*blocks: ContentBlock) -> VMessage:
    return VMessage(
        role=MessageRole.USER,
        content=[ContentBlock(kind="text", text="veja"), *blocks],
    )


def test_modalidades_do_catalogo_liberam_tipos() -> None:
    accepted = media_for_modalities({"text", "image", "file", "audio"})
    assert set(accepted) == {"application/pdf", "audio/mpeg", "audio/wav"}
    assert media_for_modalities({"text", "image"}) == {}


async def test_resolve_usa_tabela_fixa_e_catalogo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.api.handlers import provider_routing

    async def openrouter(model: str) -> set[str] | None:
        return {"text", "file"} if model == "google/gemini-2.5-flash" else None

    async def nine(model: str) -> set[str] | None:
        return {"text", "video"}

    monkeypatch.setattr(provider_routing, "openrouter_model_modalities", openrouter)
    monkeypatch.setattr(provider_routing, "nine_router_model_modalities", nine)

    assert "application/pdf" in await resolve_native_media("anthropic:claude")
    assert set(await resolve_native_media("openrouter:google/gemini-2.5-flash")) == {
        "application/pdf"
    }
    assert await resolve_native_media("openrouter:desconhecido") == {}
    assert set(await resolve_native_media("nine_router:x")) == {"video/*"}
    assert await resolve_native_media("llamacpp:qwen") == {}


async def test_catalogo_indisponivel_resulta_em_texto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.api.handlers import provider_routing

    async def broken(_model: str) -> set[str] | None:
        raise RuntimeError("sem rede")

    monkeypatch.setattr(provider_routing, "openrouter_model_modalities", broken)
    assert await resolve_native_media("openrouter:x") == {}


def test_openrouter_payload_com_pdf_audio_e_video_nativos() -> None:
    from backend.llm.openrouter.chat_client import _to_openrouter_content

    accepted = media_for_modalities({"file", "audio", "video"})
    parts = _to_openrouter_content(
        _user(
            _media("application/pdf", PDF_URL),
            _media("audio/mpeg", MP3_URL, kind="audio"),
            _media("video/mp4", "data:video/mp4;base64,AAAA", kind="video"),
        ).content,
        accepted,
    )
    assert isinstance(parts, list)
    assert parts[1] == {
        "type": "file",
        "file": {"filename": "anexo", "file_data": PDF_URL},
    }
    assert parts[2] == {
        "type": "input_audio",
        "input_audio": {"data": "SUQz", "format": "mp3"},
    }
    assert parts[3] == {
        "type": "video_url",
        "video_url": {"url": "data:video/mp4;base64,AAAA"},
    }


def test_modelo_sem_modalidade_de_arquivo_recebe_texto() -> None:
    content = _user(_media("application/pdf", PDF_URL)).content
    prepared = prepare_content(content, media_for_modalities({"text", "image"}))
    assert prepared[1] == ContentBlock(kind="text", text="[texto de application/pdf]")


async def test_cliente_openrouter_consulta_catalogo_so_com_arquivo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.llm.openrouter.chat_client import OpenRouterChatClient

    calls: list[str] = []

    async def resolve(model_id: str) -> dict[str, int]:
        calls.append(model_id)
        return media_for_modalities({"file"})

    monkeypatch.setattr(
        "backend.llm.openrouter.chat_client.resolve_native_media", resolve
    )
    sent: list[dict] = []

    class _Http:
        async def stream_sse(self, _path: str, body: dict):
            sent.append(body)
            yield {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}

    client = OpenRouterChatClient(
        "google/gemini-2.5-flash",
        cast("OpenRouterClient", _Http()),
        media_model_id="openrouter:google/gemini-2.5-flash",
    )
    chunks: list[VMessageChunk] = [
        c async for c in client.astream([_user(_media("application/pdf", PDF_URL))])
    ]
    assert chunks
    assert calls == ["openrouter:google/gemini-2.5-flash"]
    assert sent[0]["messages"][0]["content"][1]["type"] == "file"

    calls.clear()
    _ = [c async for c in client.astream([_user()])]
    assert calls == []
