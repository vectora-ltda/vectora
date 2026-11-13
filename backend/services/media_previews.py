"""Imagens geradas de PDF e vídeo: páginas, quadros e prévias de anexos.

Usadas de dois jeitos:

- **Prévias de anexo.** Quando o modelo lê imagem, mas não recebe o arquivo
  original (por exemplo, modelos de gateway que declaram só ``text`` e
  ``image``), a entrada do anexo gera as páginas ou quadros e os salva como
  assets da sessão; o bloco ``media`` guarda só as referências.
- **Ferramentas** ``pdf_to_images`` e ``video_to_frames``, em que o agente
  escolhe faixa de páginas, intervalo de tempo, resolução, qualidade e formato.

Os padrões vêm de ``backend/services/media_limits.py`` (configurações do app);
cada chamada pode pedir outros valores dentro dos tetos de lá. PDF usa o
pypdfium2; vídeo usa o ffmpeg embutido do app.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from backend.services.media_limits import (
    MAX_SIDE_RANGE,
    QUALITY_RANGE,
    clamp,
    media_limits,
)

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)

ImageFormat = Literal["jpeg", "png"]


@dataclass(frozen=True, slots=True)
class ImageOptions:
    """Resolução, qualidade e formato das imagens geradas."""

    max_side: int
    quality: int
    image_format: ImageFormat = "jpeg"

    @property
    def mime_type(self) -> str:
        return "image/png" if self.image_format == "png" else "image/jpeg"

    @property
    def extension(self) -> str:
        return "png" if self.image_format == "png" else "jpg"


def image_options(
    max_side: int | None = None,
    quality: int | None = None,
    image_format: str = "jpeg",
) -> ImageOptions:
    """Opções pedidas, limitadas aos tetos; ausente usa a configuração."""
    limits = media_limits()
    return ImageOptions(
        max_side=clamp(max_side or limits.max_side, MAX_SIDE_RANGE, limits.max_side),
        quality=clamp(quality or limits.quality, QUALITY_RANGE, limits.quality),
        image_format="png" if image_format.lower() == "png" else "jpeg",
    )


def _encode(image: Image.Image, options: ImageOptions) -> bytes:
    """Reduz a imagem PIL ao lado máximo e codifica no formato pedido."""
    rgb = image.convert("RGB")
    rgb.thumbnail((options.max_side, options.max_side))
    buffer = io.BytesIO()
    if options.image_format == "png":
        rgb.save(buffer, format="PNG", optimize=True)
    else:
        rgb.save(buffer, format="JPEG", quality=options.quality, optimize=True)
    return buffer.getvalue()


def pdf_page_count(payload: bytes) -> int:
    """Número de páginas do PDF; 0 se não puder ser aberto."""
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(payload)
    except Exception:
        return 0
    try:
        return len(document)
    finally:
        document.close()


def render_pdf_pages(
    payload: bytes,
    max_pages: int | None = None,
    *,
    first_page: int = 1,
    options: ImageOptions | None = None,
) -> list[bytes]:
    """Renderiza páginas do PDF a partir de ``first_page`` (começa em 1).

    Devolve uma imagem por página, em ordem; lista vazia se o PDF não puder
    ser aberto. ``max_pages`` vazio usa ``media_pdf_max_pages``.
    """
    options = options or image_options()
    count = max_pages or media_limits().pdf_pages
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(payload)
    except Exception:
        logger.warning("media_previews: PDF não pôde ser aberto para renderizar")
        return []
    pages: list[bytes] = []
    try:
        start = max(first_page, 1) - 1
        for index in range(start, min(len(document), start + count)):
            page = document[index]
            try:
                width, height = page.get_size()
                scale = options.max_side / max(width, height, 1)
                bitmap = page.render(scale=max(scale, 0.1))
                pages.append(_encode(bitmap.to_pil(), options))
            finally:
                page.close()
    except Exception:
        logger.exception("media_previews: falha ao renderizar página do PDF")
    finally:
        document.close()
    return pages


def frame_timestamps(
    duration_s: float,
    *,
    count: int,
    start_ms: int = 0,
    end_ms: int = 0,
    interval_ms: int = 0,
) -> list[float]:
    """Instantes (em segundos) dos quadros a extrair.

    - Com ``interval_ms`` > 0, um quadro a cada intervalo, a partir de
      ``start_ms`` e antes de ``end_ms`` (0 = fim do vídeo), até ``count``.
    - Sem intervalo, ``count`` quadros espaçados de forma uniforme no trecho,
      cada um no centro da sua fatia (evita quadro preto no começo e no fim).
    """
    if duration_s <= 0 or count <= 0:
        return []
    start = min(max(start_ms, 0) / 1000, duration_s)
    end = min(end_ms / 1000, duration_s) if end_ms > 0 else duration_s
    if end <= start:
        return []
    if interval_ms > 0:
        step = interval_ms / 1000
        stamps: list[float] = []
        moment = start
        while moment < end and len(stamps) < count:
            stamps.append(round(moment, 3))
            moment += step
        return stamps
    span = end - start
    return [round(start + span * (index + 0.5) / count, 3) for index in range(count)]


async def video_duration_s(path: Path) -> float | None:
    """Duração do vídeo em segundos (ffprobe embutido)."""
    from backend.services.ffmpeg_binary import resolve_ffprobe
    from backend.tools.media_native import _run

    ffprobe = resolve_ffprobe()
    if ffprobe is None:
        return None
    code, out, _err = await _run(
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    )
    if code != 0:
        return None
    try:
        return float(json.loads(out)["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        return None


async def extract_video_frames(
    path: Path,
    count: int | None = None,
    *,
    start_ms: int = 0,
    end_ms: int = 0,
    interval_ms: int = 0,
    options: ImageOptions | None = None,
) -> list[tuple[float, bytes]]:
    """Quadros do vídeo como ``(segundo, imagem)``; vazio se falhar.

    ``count`` vazio usa ``media_video_max_frames``. Veja ``frame_timestamps``
    para como ``start_ms``, ``end_ms`` e ``interval_ms`` escolhem os instantes.
    """
    from PIL import Image

    from backend.services.ffmpeg_binary import resolve_ffmpeg
    from backend.tools.media_native import extract_frame_to

    options = options or image_options()
    ffmpeg = resolve_ffmpeg()
    duration = await video_duration_s(path)
    if ffmpeg is None or not duration:
        return []
    stamps = frame_timestamps(
        duration,
        count=count or media_limits().video_frames,
        start_ms=start_ms,
        end_ms=end_ms,
        interval_ms=interval_ms,
    )
    frames: list[tuple[float, bytes]] = []
    with tempfile.TemporaryDirectory(prefix="vectora-frames-") as workdir:
        for index, moment in enumerate(stamps):
            out_path = Path(workdir) / f"frame-{index}.png"
            ok, _err = await extract_frame_to(ffmpeg, str(path), moment, str(out_path))
            if not ok:
                continue
            with Image.open(out_path) as image:
                frames.append((moment, _encode(image, options)))
    return frames


async def build_previews(
    payload: bytes,
    *,
    mime_type: str,
    path: Path | None,
    thread_id: str,
    user_id: str,
    workspace_id: str,
) -> tuple[list[str], str]:
    """Gera e salva as prévias de um anexo; devolve ``(asset_ids, legenda)``.

    A legenda situa cada imagem (páginas ou instantes) e lembra a ferramenta
    para ver outras páginas, outro trecho do vídeo ou outra resolução.
    """
    from backend.services.chat_attachments import persist_media
    from backend.services.media_types import MediaInfo

    options = image_options()
    if mime_type == "application/pdf":
        images = await asyncio.to_thread(render_pdf_pages, payload, options=options)
        total = await asyncio.to_thread(pdf_page_count, payload)
        caption = ""
        if images:
            caption = f"Páginas 1–{len(images)} de {total} do documento como imagem."
            if len(images) < total:
                caption += " Use `pdf_to_images` para ver as demais."
    elif mime_type.startswith("video/") and path is not None:
        frames = await extract_video_frames(path, options=options)
        images = [image for _moment, image in frames]
        duration = await video_duration_s(path)
        caption = ""
        if frames:
            caption = (
                f"{len(frames)} quadros do vídeo nos segundos "
                + ", ".join(f"{moment:.1f}" for moment, _image in frames)
                + (f", de {duration:.1f} s no total" if duration else "")
                + ". Use `video_to_frames` para outro trecho, intervalo ou resolução."
            )
    else:
        return [], ""

    asset_ids: list[str] = []
    info = MediaInfo(kind="image", mime_type=options.mime_type)
    for image in images:
        saved = persist_media(
            image,
            info,
            thread_id=thread_id,
            user_id=user_id,
            workspace_id=workspace_id,
        )
        if saved is not None:
            asset_ids.append(saved[1])
    return asset_ids, caption if asset_ids else ""
