"""Limites configuráveis das imagens geradas de PDF e vídeo.

Valem para as prévias criadas na entrada do anexo e como padrão das
ferramentas ``pdf_to_images`` e ``video_to_frames``. Cada chamada de
ferramenta pode pedir outros valores dentro dos tetos abaixo.

Configurações (``backend/config/fields.py``), todas opcionais:

- ``media_pdf_max_pages``: páginas de PDF por anexo e por chamada.
- ``media_video_max_frames``: quadros de vídeo por anexo e por chamada.
- ``media_image_max_side``: lado maior das imagens, em pixels.
- ``media_image_quality``: qualidade JPEG (aceita de 30 a 95).
- ``media_max_images``: teto de imagens por chamada de ferramenta e de
  imagens de ferramenta mantidas no contexto do modelo.
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_PDF_PAGES = 20
DEFAULT_VIDEO_FRAMES = 12
DEFAULT_MAX_SIDE = 1280
DEFAULT_QUALITY = 80
DEFAULT_MAX_IMAGES = 60

#: (mínimo, máximo) aceitos para cada valor.
PDF_PAGES_RANGE = (1, 500)
VIDEO_FRAMES_RANGE = (1, 300)
MAX_SIDE_RANGE = (256, 4096)
QUALITY_RANGE = (30, 95)
MAX_IMAGES_RANGE = (8, 500)


@dataclass(frozen=True, slots=True)
class MediaLimits:
    """Valores efetivos, já validados e dentro dos tetos."""

    pdf_pages: int = DEFAULT_PDF_PAGES
    video_frames: int = DEFAULT_VIDEO_FRAMES
    max_side: int = DEFAULT_MAX_SIDE
    quality: int = DEFAULT_QUALITY
    max_images: int = DEFAULT_MAX_IMAGES


def clamp(value: object, bounds: tuple[int, int], default: int) -> int:
    """Número dentro dos limites; valor ausente ou inválido usa ``default``."""
    low, high = bounds
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _setting(key: str) -> object:
    try:
        from backend.workspace.runtime_settings import runtime_settings

        return runtime_settings.get(key)
    except Exception:
        return None


def media_limits() -> MediaLimits:
    """Limites efetivos a partir das configurações do app."""
    return MediaLimits(
        pdf_pages=clamp(
            _setting("media_pdf_max_pages"), PDF_PAGES_RANGE, DEFAULT_PDF_PAGES
        ),
        video_frames=clamp(
            _setting("media_video_max_frames"), VIDEO_FRAMES_RANGE, DEFAULT_VIDEO_FRAMES
        ),
        max_side=clamp(
            _setting("media_image_max_side"), MAX_SIDE_RANGE, DEFAULT_MAX_SIDE
        ),
        quality=clamp(_setting("media_image_quality"), QUALITY_RANGE, DEFAULT_QUALITY),
        max_images=clamp(
            _setting("media_max_images"), MAX_IMAGES_RANGE, DEFAULT_MAX_IMAGES
        ),
    )


#: Campo de configuração, valor padrão e faixa aceita de cada limite, na ordem
#: em que a interface os mostra.
LIMIT_FIELDS: dict[str, tuple[str, int, tuple[int, int]]] = {
    "pdf_pages": ("media_pdf_max_pages", DEFAULT_PDF_PAGES, PDF_PAGES_RANGE),
    "video_frames": (
        "media_video_max_frames",
        DEFAULT_VIDEO_FRAMES,
        VIDEO_FRAMES_RANGE,
    ),
    "max_side": ("media_image_max_side", DEFAULT_MAX_SIDE, MAX_SIDE_RANGE),
    "quality": ("media_image_quality", DEFAULT_QUALITY, QUALITY_RANGE),
    "max_images": ("media_max_images", DEFAULT_MAX_IMAGES, MAX_IMAGES_RANGE),
}


def limits_settings() -> dict[str, object]:
    """Valores efetivos, padrões e faixas aceitas, para a interface."""
    effective = media_limits()
    return {
        name: {
            "value": getattr(effective, name),
            "default": default,
            "min": bounds[0],
            "max": bounds[1],
        }
        for name, (_field, default, bounds) in LIMIT_FIELDS.items()
    }


def validate_limit(name: str, value: object) -> int:
    """Número inteiro dentro da faixa do limite; do contrário, ``ValueError``."""
    _field, _default, (low, high) = LIMIT_FIELDS[name]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} deve ser um número inteiro")
    if not low <= value <= high:
        raise ValueError(f"{name} deve estar entre {low} e {high}")
    return value
