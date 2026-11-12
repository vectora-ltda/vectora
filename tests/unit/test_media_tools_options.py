"""Limites e opções das imagens geradas de PDF e vídeo."""

from __future__ import annotations

import base64
import io
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image

from backend.services import media_limits
from backend.services.assets import AssetStore
from backend.services.media_previews import (
    frame_timestamps,
    image_options,
    pdf_page_count,
    render_pdf_pages,
)
from backend.settings import settings
from backend.tools.context import ToolContext
from backend.tools.media_native import pdf_to_images, video_to_frames

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg necessário"
)


def _pdf(pages: int) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=600, height=800)
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _make_video(path: Path, seconds: int) -> None:
    subprocess.run(  # noqa: S603
        [
            str(shutil.which("ffmpeg")),
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={seconds}:size=320x240:rate=10",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )


@pytest.fixture
def values(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    """Configurações do app em memória (as chaves ``media_*``)."""
    store: dict[str, object] = {}
    monkeypatch.setattr(media_limits, "_setting", store.get)
    return store


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, "vectora_home", tmp_path)
    monkeypatch.setattr("backend.services.assets.asset_store", AssetStore())
    return tmp_path


def _ctx() -> ToolContext:
    return ToolContext(user_id="u1", workspace_id="w1", thread_id="t1")


class TestLimites:
    def test_padroes_sao_bem_maiores_que_antes(self, values: dict[str, object]) -> None:
        limits = media_limits.media_limits()
        assert (limits.pdf_pages, limits.video_frames) == (20, 12)
        assert (limits.max_side, limits.quality, limits.max_images) == (1280, 80, 60)

    def test_configuracao_vence_e_respeita_os_tetos(
        self, values: dict[str, object]
    ) -> None:
        values.update(
            media_pdf_max_pages="120",
            media_video_max_frames=40,
            media_image_max_side="99999",
            media_image_quality=1,
            media_max_images="nada",
        )
        limits = media_limits.media_limits()
        assert limits.pdf_pages == 120
        assert limits.video_frames == 40
        assert limits.max_side == 4096
        assert limits.quality == 30
        assert limits.max_images == 60

    def test_opcoes_da_chamada_ficam_dentro_dos_tetos(
        self, values: dict[str, object]
    ) -> None:
        options = image_options(max_side=99999, quality=5, image_format="PNG")
        assert (options.max_side, options.quality) == (4096, 30)
        assert options.image_format == "png"
        assert image_options().max_side == 1280
        assert image_options(image_format="webp").image_format == "jpeg"


class TestInstantesDosQuadros:
    def test_quadros_uniformes_no_centro_de_cada_fatia(self) -> None:
        assert frame_timestamps(10, count=4) == [1.25, 3.75, 6.25, 8.75]

    def test_intervalo_em_ms_dentro_do_trecho(self) -> None:
        stamps = frame_timestamps(
            10, count=10, start_ms=1000, end_ms=8000, interval_ms=2500
        )
        assert stamps == [1.0, 3.5, 6.0]

    def test_quantidade_maxima_corta_o_intervalo(self) -> None:
        assert frame_timestamps(10, count=3, interval_ms=1000) == [0.0, 1.0, 2.0]

    def test_trecho_vazio_ou_fora_do_video(self) -> None:
        assert frame_timestamps(10, count=3, start_ms=9000, end_ms=2000) == []
        assert frame_timestamps(10, count=3, start_ms=99_000) == []
        assert frame_timestamps(0, count=3) == []


class TestPaginasDoPdf:
    def test_faixa_de_paginas_e_contagem(self) -> None:
        pdf = _pdf(5)
        assert pdf_page_count(pdf) == 5
        assert len(render_pdf_pages(pdf, 2, first_page=2)) == 2
        assert len(render_pdf_pages(pdf, 10, first_page=4)) == 2

    def test_resolucao_e_formato_pedidos(self) -> None:
        pdf = _pdf(1)
        [small] = render_pdf_pages(pdf, 1, options=image_options(max_side=300))
        [large] = render_pdf_pages(pdf, 1, options=image_options(max_side=1600))
        assert max(Image.open(io.BytesIO(small)).size) <= 300
        assert max(Image.open(io.BytesIO(large)).size) > 1000
        [png] = render_pdf_pages(pdf, 1, options=image_options(image_format="png"))
        assert png.startswith(b"\x89PNG")


class TestFerramentaPdfToImages:
    async def _run(self, home: Path, pdf_bytes: bytes, **kwargs: Any) -> dict:
        pdf = home / "doc.pdf"
        pdf.write_bytes(pdf_bytes)
        with patch("backend.tools.fs._confine", return_value=(pdf, "")):
            return json.loads(await pdf_to_images(ctx=_ctx(), path="doc.pdf", **kwargs))

    async def test_padrao_vem_da_configuracao(
        self, home: Path, values: dict[str, object]
    ) -> None:
        values["media_pdf_max_pages"] = 7
        result = await self._run(home, _pdf(30))
        assert (result["total_pages"], result["returned"]) == (30, 7)
        assert result["next_page"] == 8

    async def test_faixa_resolucao_e_formato(
        self, home: Path, values: dict[str, object]
    ) -> None:
        result = await self._run(
            home,
            _pdf(10),
            first_page=3,
            last_page=5,
            max_side=400,
            image_format="png",
        )
        assert [page["page"] for page in result["pages"]] == [3, 4, 5]
        assert result["next_page"] == 6
        assert len(result["model_images"]) == 3
        assert all(ref["mime_type"] == "image/png" for ref in result["model_images"])

    async def test_teto_por_chamada_e_ultima_pagina(
        self, home: Path, values: dict[str, object]
    ) -> None:
        values["media_max_images"] = 8
        result = await self._run(home, _pdf(30), max_pages=500)
        assert result["returned"] == 8
        tail = await self._run(home, _pdf(30), first_page=29, max_pages=10)
        assert (tail["returned"], tail["next_page"]) == (2, None)

    async def test_erros(self, home: Path, values: dict[str, object]) -> None:
        assert "error" in await self._run(home, _pdf(3), first_page=9)
        assert "error" in await self._run(home, b"%PDF-quebrado")


@requires_ffmpeg
class TestFerramentaVideoToFrames:
    async def _run(self, home: Path, seconds: int = 3, **kwargs: Any) -> dict:
        video = home / "clip.mp4"
        _make_video(video, seconds)
        with patch("backend.tools.fs._confine", return_value=(video, "")):
            return json.loads(
                await video_to_frames(ctx=_ctx(), path="clip.mp4", **kwargs)
            )

    async def test_um_quadro_por_intervalo_em_ms(
        self, home: Path, values: dict[str, object]
    ) -> None:
        result = await self._run(home, interval_ms=1000)
        assert [frame["time_ms"] for frame in result["frames"]] == [0, 1000, 2000]
        assert result["duration_ms"] >= 2900
        assert result["truncated"] is False
        assert len(result["model_images"]) == 3

    async def test_limite_de_quadros_avisa_onde_continuar(
        self, home: Path, values: dict[str, object]
    ) -> None:
        result = await self._run(home, interval_ms=500, max_frames=3)
        assert result["returned"] == 3
        assert result["truncated"] is True
        assert result["next_start_ms"] == 1500

    async def test_trecho_e_resolucao(
        self, home: Path, values: dict[str, object]
    ) -> None:
        result = await self._run(
            home, start_ms=1000, end_ms=2000, max_frames=2, max_side=256
        )
        times = [frame["time_ms"] for frame in result["frames"]]
        assert all(1000 <= moment < 2000 for moment in times)
        assert len(times) == 2

    async def test_trecho_fora_do_video(
        self, home: Path, values: dict[str, object]
    ) -> None:
        result = await self._run(home, start_ms=900_000)
        assert "error" in result


@requires_ffmpeg
async def test_anexo_respeita_os_limites_configurados(
    home: Path, values: dict[str, object]
) -> None:
    from backend.api.handlers.chat import _build_user_vmessage
    from backend.api.schemas import Attachment, AttachmentKind

    values["media_pdf_max_pages"] = 3
    message = await _build_user_vmessage(
        "resuma",
        [
            Attachment(
                kind=AttachmentKind.DOCUMENT,
                name="r.pdf",
                mime_type="application/pdf",
                base64_data=base64.b64encode(_pdf(9)).decode(),
            )
        ],
        "t1",
        user_id="u1",
        workspace_id="w1",
    )
    block = message.content[1]
    assert block.media_preview_asset_ids is not None
    assert len(block.media_preview_asset_ids) == 3
    assert block.media_preview_caption is not None
    assert "Páginas 1–3 de 9" in block.media_preview_caption
    assert "pdf_to_images" in block.media_preview_caption
