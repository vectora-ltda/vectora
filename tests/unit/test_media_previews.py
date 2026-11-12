"""PDF e vídeo como imagem para modelos que leem imagem, mas não o arquivo."""

from __future__ import annotations

import asyncio
import base64
import io
import shutil
from pathlib import Path

import pytest

from backend.llm.media_parts import (
    media_for_modalities,
    media_routing_evidence,
    prepare_content,
)
from backend.services.assets import AssetStore
from backend.services.media_hydration import hydrate_media_blocks
from backend.services.media_previews import extract_video_frames, render_pdf_pages
from backend.settings import settings
from backend.vtypes.message import ContentBlock, MessageRole, VMessage


def _pdf(pages: int = 2) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=300, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "vectora_home", tmp_path)
    monkeypatch.setattr("backend.services.assets.asset_store", AssetStore())
    return tmp_path


def test_pdf_vira_paginas_jpeg() -> None:
    pages = render_pdf_pages(_pdf(3))
    assert len(pages) == 3
    assert all(page.startswith(b"\xff\xd8\xff") for page in pages)


def test_pdf_invalido_nao_gera_paginas() -> None:
    assert render_pdf_pages(b"%PDF-quebrado") == []


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg necessário")
def test_video_vira_quadros_espacados(tmp_path: Path) -> None:
    import subprocess

    video = tmp_path / "clip.mp4"
    subprocess.run(  # noqa: S603
        [
            str(shutil.which("ffmpeg")),
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=3:size=160x120:rate=10",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
    )
    frames = asyncio.run(extract_video_frames(video, count=3))
    assert [round(ts, 1) for ts, _ in frames] == [0.5, 1.5, 2.5]
    assert all(image.startswith(b"\xff\xd8\xff") for _ts, image in frames)


async def test_pdf_anexado_ganha_previas_carregadas_no_envio(home: Path) -> None:
    from backend.api.handlers.chat import _build_user_vmessage
    from backend.api.schemas import Attachment, AttachmentKind

    message = await _build_user_vmessage(
        "resuma",
        [
            Attachment(
                kind=AttachmentKind.DOCUMENT,
                name="r.pdf",
                mime_type="application/pdf",
                base64_data=base64.b64encode(_pdf(2)).decode(),
            )
        ],
        "t1",
        user_id="u1",
        workspace_id="w1",
    )
    block = message.content[1]
    assert block.media_preview_asset_ids and len(block.media_preview_asset_ids) == 2
    assert block.media_preview_caption
    assert block.media_preview_urls is None

    [sent] = hydrate_media_blocks(
        [message], user_id="u1", workspace_id="w1", thread_id="t1"
    )
    urls = sent.content[1].media_preview_urls
    assert urls and all(url.startswith("data:image/jpeg;base64,") for url in urls)
    # Os bytes das prévias nunca vão para o histórico persistido.
    assert "media_preview_urls" not in message.to_dict()["content"][1] or (
        message.to_dict()["content"][1]["media_preview_urls"] is None
    )


def _pdf_block(*, native: bool) -> ContentBlock:
    return ContentBlock(
        kind="media",
        media_kind="document",
        text="[r.pdf] texto extraído",
        attachment_name="r.pdf",
        attachment_mime_type="application/pdf",
        attachment_size_bytes=10,
        media_data_url="data:application/pdf;base64,JVBERi0=" if native else None,
        media_preview_caption="Páginas 1–2 do documento como imagem.",
        media_preview_urls=["data:image/jpeg;base64,AAA", "data:image/jpeg;base64,BBB"],
    )


def test_modelo_so_com_imagem_recebe_texto_legenda_e_paginas() -> None:
    accepted = media_for_modalities({"text", "image"})
    prepared = prepare_content([_pdf_block(native=True)], accepted)
    assert [block.kind for block in prepared] == [
        "text",
        "text",
        "image_url",
        "image_url",
    ]
    assert prepared[0].text == "[r.pdf] texto extraído"
    assert prepared[2].image_url == "data:image/jpeg;base64,AAA"
    [entry] = media_routing_evidence(
        [VMessage(role=MessageRole.USER, content=[_pdf_block(native=True)])],
        accepted,
        "nine_router:auto",
    )
    assert entry["mode"] == "image"
    assert "2 prévias" in str(entry["reason"])


def test_arquivo_nativo_tem_prioridade_e_sem_imagem_vira_texto() -> None:
    assert prepare_content([_pdf_block(native=True)], "anthropic")[0].kind == "media"
    assert [b.kind for b in prepare_content([_pdf_block(native=True)], {})] == ["text"]


def test_openrouter_envia_paginas_como_image_url() -> None:
    from backend.llm.openrouter.chat_client import _to_openrouter_content

    parts = _to_openrouter_content(
        [ContentBlock(kind="text", text="veja"), _pdf_block(native=True)],
        media_for_modalities({"text", "image"}),
    )
    assert isinstance(parts, list)
    assert [part["type"] for part in parts] == [
        "text",
        "text",
        "text",
        "image_url",
        "image_url",
    ]
