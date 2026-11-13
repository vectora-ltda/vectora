"""Classificação de anexos: tipo efetivo, MIME pelos bytes e limites.

Fonte única para o chat decidir o que um anexo é. O MIME declarado pelo
cliente é só uma dica: a assinatura dos bytes prevalece, e um arquivo que
não casa com nenhum formato conhecido é tratado como arquivo genérico
(``application/octet-stream``), nunca recusado só pelo tipo.

Tipos (``MediaKind``):

- ``image``: PNG, JPEG, GIF, WebP.
- ``audio``: MP3, WAV, OGG, WebM (áudio), M4A, FLAC.
- ``video``: MP4, WebM, QuickTime.
- ``document``: PDF, DOCX, XLSX, PPTX, ODT/ODS/ODP.
- ``text``: qualquer conteúdo UTF-8 sem bytes nulos (código, logs, CSV,
  JSON, HAR, Markdown...).
- ``file``: o resto; vai ao agente como referência com caminho, não como
  conteúdo.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePath
from typing import Literal

MediaKind = Literal["image", "audio", "video", "document", "text", "file"]

MB = 1024 * 1024

#: Teto por tipo. O conteúdo viaja em base64 dentro do JSON do chat, então
#: os tetos ficam abaixo do limite do ``AssetStore``.
MAX_BYTES_BY_KIND: dict[MediaKind, int] = {
    "image": 10 * MB,
    "audio": 25 * MB,
    "video": 50 * MB,
    "document": 25 * MB,
    "text": 10 * MB,
    "file": 25 * MB,
}
#: Arquivos HAR são texto, mas costumam ser grandes.
MAX_HAR_BYTES = 50 * MB
#: Maior teto entre todos os tipos; usado antes de decodificar o base64.
MAX_ATTACHMENT_BYTES = max(*MAX_BYTES_BY_KIND.values(), MAX_HAR_BYTES)

OCTET_STREAM = "application/octet-stream"

_OOXML_BY_DIRECTORY = {
    "word/": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xl/": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt/": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}
DOCX_MIME = _OOXML_BY_DIRECTORY["word/"]
XLSX_MIME = _OOXML_BY_DIRECTORY["xl/"]
_ODF_MIMES = {
    "application/vnd.oasis.opendocument.text",
    "application/vnd.oasis.opendocument.spreadsheet",
    "application/vnd.oasis.opendocument.presentation",
}

DOCUMENT_MIMES = frozenset(
    {"application/pdf", *_OOXML_BY_DIRECTORY.values(), *_ODF_MIMES}
)

#: Extensão canônica por MIME, usada ao gravar o asset.
EXTENSION_BY_MIME: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/ogg": ".ogg",
    "audio/webm": ".webm",
    "audio/mp4": ".m4a",
    "audio/flac": ".flac",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "application/pdf": ".pdf",
    _OOXML_BY_DIRECTORY["word/"]: ".docx",
    _OOXML_BY_DIRECTORY["xl/"]: ".xlsx",
    _OOXML_BY_DIRECTORY["ppt/"]: ".pptx",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/vnd.oasis.opendocument.presentation": ".odp",
    OCTET_STREAM: ".bin",
}


@dataclass(frozen=True, slots=True)
class MediaInfo:
    """Resultado da classificação de um anexo."""

    kind: MediaKind
    mime_type: str


def _zip_mime(data: bytes) -> str | None:
    """Identifica OOXML/ODF dentro de um ZIP sem extrair conteúdo."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = archive.namelist()
            if "mimetype" in names:
                declared = archive.read("mimetype")[:128].decode("ascii", "ignore")
                if declared.strip() in _ODF_MIMES:
                    return declared.strip()
            for prefix, mime in _OOXML_BY_DIRECTORY.items():
                if any(name.startswith(prefix) for name in names):
                    return mime
    except (zipfile.BadZipFile, OSError, ValueError, KeyError, RuntimeError):
        return None
    return None


def _iso_media_mime(data: bytes, name: str) -> str | None:
    """Distingue MP4/M4A/QuickTime pelo ``ftyp``."""
    if len(data) < 12 or data[4:8] != b"ftyp":
        return None
    brand = data[8:12]
    if brand == b"qt  ":
        return "video/quicktime"
    if brand in {b"M4A ", b"M4B "} or PurePath(name).suffix.lower() == ".m4a":
        return "audio/mp4"
    return "video/mp4"


def _riff_mime(data: bytes) -> str | None:
    if len(data) < 12 or data[:4] != b"RIFF":
        return None
    if data[8:12] == b"WEBP":
        return "image/webp"
    if data[8:12] == b"WAVE":
        return "audio/wav"
    return None


_SIMPLE_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"%PDF-", "application/pdf"),
    (b"ID3", "audio/mpeg"),
    (b"fLaC", "audio/flac"),
    (b"OggS", "audio/ogg"),
)


def _simple_mime(data: bytes, _name: str) -> str | None:
    for signature, mime in _SIMPLE_SIGNATURES:
        if data.startswith(signature):
            return mime
    if len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0:
        return "audio/mpeg"
    return None


def _matroska_mime(data: bytes, name: str) -> str | None:
    if not data.startswith(b"\x1a\x45\xdf\xa3"):
        return None
    # Matroska/WebM: sem parse de trilhas, a extensão desempata.
    return "audio/webm" if name.lower().endswith((".weba", ".ogg")) else "video/webm"


def _zip_container_mime(data: bytes, _name: str) -> str | None:
    return _zip_mime(data) if data.startswith(b"PK\x03\x04") else None


def sniff_mime(data: bytes, name: str = "") -> str | None:
    """MIME pelos bytes; ``None`` quando nenhuma assinatura conhecida casa."""
    for detector in (
        _simple_mime,
        lambda payload, _n: _riff_mime(payload),
        _iso_media_mime,
        _matroska_mime,
        _zip_container_mime,
    ):
        mime = detector(data, name)
        if mime is not None:
            return mime
    return None


#: Contêineres que servem tanto a áudio quanto a vídeo: o MIME declarado
#: desempata (gravação de voz do navegador chega como ``audio/webm``).
_AUDIO_TWIN = {"video/webm": "audio/webm", "video/mp4": "audio/mp4"}


def _looks_like_text(data: bytes) -> bool:
    if b"\x00" in data[:8192]:
        return False
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True


def kind_for_mime(mime_type: str) -> MediaKind:
    """Tipo efetivo de um MIME já confirmado pelos bytes."""
    if mime_type.startswith("image/"):
        return "image"
    if mime_type.startswith("audio/"):
        return "audio"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type in DOCUMENT_MIMES:
        return "document"
    if mime_type.startswith("text/"):
        return "text"
    return "file"


def classify(data: bytes, name: str = "", declared_mime: str = "") -> MediaInfo:
    """Classifica um anexo pelos bytes; o MIME declarado só nomeia texto."""
    sniffed = sniff_mime(data, name)
    if sniffed in _AUDIO_TWIN and declared_mime.lower().startswith("audio/"):
        sniffed = _AUDIO_TWIN[sniffed]
    if sniffed is not None:
        return MediaInfo(kind=kind_for_mime(sniffed), mime_type=sniffed)
    if _looks_like_text(data):
        declared = declared_mime.lower()
        textual = declared if declared.startswith("text/") else "text/plain"
        if declared in {"application/json", "application/xml"}:
            textual = declared
        return MediaInfo(kind="text", mime_type=textual)
    return MediaInfo(kind="file", mime_type=OCTET_STREAM)


def max_bytes_for(kind: MediaKind, name: str = "") -> int:
    """Teto de bytes para o tipo (HAR tem teto próprio)."""
    if kind == "text" and name.lower().endswith(".har"):
        return MAX_HAR_BYTES
    return MAX_BYTES_BY_KIND[kind]


def effective_media_info(
    data: bytes, name: str = "", declared_mime: str = "", declared_kind: str = ""
) -> MediaInfo:
    """Classificação usada pelo chat.

    Igual a ``classify``, exceto quando nenhuma assinatura casa e o cliente
    declarou áudio ou vídeo: aí vale o tipo declarado (a transcrição ou a
    ferramenta de vídeo decidem se o conteúdo é utilizável).
    """
    if declared_kind in {"audio", "video"} and sniff_mime(data, name) is None:
        return MediaInfo(
            kind="audio" if declared_kind == "audio" else "video",
            mime_type=declared_mime or OCTET_STREAM,
        )
    return classify(data, name, declared_mime)
