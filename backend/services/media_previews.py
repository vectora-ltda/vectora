"""Prévias visuais de anexos: páginas de PDF e quadros de vídeo como imagens.

Usadas quando o modelo lê imagem, mas não recebe o arquivo original (por
exemplo, modelos de gateway que declaram só ``text`` e ``image``). As prévias
são geradas uma vez, na entrada do anexo, e salvas como assets da sessão; o
bloco ``media`` guarda só as referências.

- PDF: até ``MAX_PDF_PAGES`` páginas renderizadas com pypdfium2.
- Vídeo: ``VIDEO_FRAMES`` quadros espaçados ao longo da duração, com o
  ffmpeg embutido do app.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

logger = logging.getLogger(__name__)

MAX_PDF_PAGES = 8
VIDEO_FRAMES = 6
#: Lado maior das imagens geradas, em pixels.
MAX_SIDE = 1280
JPEG_QUALITY = 80


def _to_jpeg(image: Image.Image) -> bytes:
    """Reduz a imagem PIL ao lado máximo e codifica em JPEG."""
    rgb = image.convert("RGB")
    rgb.thumbnail((MAX_SIDE, MAX_SIDE))
    buffer = io.BytesIO()
    rgb.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buffer.getvalue()


def render_pdf_pages(payload: bytes, max_pages: int = MAX_PDF_PAGES) -> list[bytes]:
    """Renderiza as primeiras páginas do PDF como JPEG; vazio se falhar."""
    try:
        import pypdfium2 as pdfium

        document = pdfium.PdfDocument(payload)
    except Exception:
        logger.warning("media_previews: PDF não pôde ser aberto para renderizar")
        return []
    pages: list[bytes] = []
    try:
        for index in range(min(len(document), max_pages)):
            page = document[index]
            try:
                width, height = page.get_size()
                scale = MAX_SIDE / max(width, height, 1)
                bitmap = page.render(scale=max(scale, 0.1))
                pages.append(_to_jpeg(bitmap.to_pil()))
            finally:
                page.close()
    except Exception:
        logger.exception("media_previews: falha ao renderizar página do PDF")
    finally:
        document.close()
    return pages


async def _video_duration(ffprobe: str, path: Path) -> float | None:
    from backend.tools.media_native import _run

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
    path: Path, count: int = VIDEO_FRAMES
) -> list[tuple[float, bytes]]:
    """Quadros espaçados do vídeo como ``(segundo, JPEG)``; vazio se falhar."""
    from PIL import Image

    from backend.services.ffmpeg_binary import resolve_ffmpeg, resolve_ffprobe
    from backend.tools.media_native import extract_frame_to

    ffmpeg, ffprobe = resolve_ffmpeg(), resolve_ffprobe()
    if ffmpeg is None or ffprobe is None:
        return []
    duration = await _video_duration(ffprobe, path)
    if not duration or duration <= 0:
        return []
    # Centro de cada intervalo: evita quadro preto no início e no fim.
    timestamps = [duration * (index + 0.5) / count for index in range(count)]
    frames: list[tuple[float, bytes]] = []
    with tempfile.TemporaryDirectory(prefix="vectora-frames-") as workdir:
        for index, timestamp in enumerate(timestamps):
            out_path = Path(workdir) / f"frame-{index}.png"
            ok, _err = await extract_frame_to(
                ffmpeg, str(path), round(timestamp, 2), str(out_path)
            )
            if not ok:
                continue
            with Image.open(out_path) as image:
                frames.append((timestamp, _to_jpeg(image)))
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
    """Gera e salva as prévias; devolve ``(asset_ids, legenda)``.

    A legenda descreve o que as imagens são (páginas ou quadros com tempo)
    para o modelo situar cada imagem.
    """
    from backend.services.chat_attachments import persist_media
    from backend.services.media_types import MediaInfo

    if mime_type == "application/pdf":
        images = await asyncio.to_thread(render_pdf_pages, payload)
        caption = f"Páginas 1–{len(images)} do documento como imagem." if images else ""
        labelled = list(images)
    elif mime_type.startswith("video/") and path is not None:
        frames = await extract_video_frames(path)
        labelled = [image for _ts, image in frames]
        caption = (
            "Quadros do vídeo nos segundos "
            + ", ".join(f"{ts:.1f}" for ts, _image in frames)
            + "."
            if frames
            else ""
        )
    else:
        return [], ""

    asset_ids: list[str] = []
    info = MediaInfo(kind="image", mime_type="image/jpeg")
    for image in labelled:
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
