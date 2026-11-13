"""Entrada de anexos do chat que não são imagem.

Cada anexo vira um ``ContentBlock(kind="media")`` com:

- ``text``: a forma textual que qualquer modelo recebe (texto do arquivo,
  texto extraído do documento, transcrição do áudio ou uma referência ao
  arquivo salvo);
- ``asset_id``/``media_sha256``: os bytes salvos na mídia da sessão, para que
  o envio nativo e as ferramentas de mídia (``analyze_video``,
  ``audio_transcribe``) tenham o arquivo original.

O tipo vem dos bytes (``media_types.classify``), não do que o cliente
declarou.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path

from backend.services.media_types import (
    DOCX_MIME,
    EXTENSION_BY_MIME,
    XLSX_MIME,
    MediaInfo,
    MediaKind,
)
from backend.vtypes.message import ContentBlock

logger = logging.getLogger(__name__)

_SAFE_THREAD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
#: Teto de texto extraído de um documento antes do planejador de contexto.
MAX_EXTRACTED_CHARS = 200_000

_LANG_BY_EXTENSION = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".php": "php",
    ".sh": "bash",
    ".bash": "bash",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".har": "json",
    ".xml": "xml",
    ".html": "html",
    ".css": "css",
    ".sql": "sql",
    ".graphql": "graphql",
    ".r": "r",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".md": "markdown",
    ".toml": "toml",
    ".csv": "csv",
}

_LABEL_BY_KIND: dict[MediaKind, str] = {
    "image": "Imagem",
    "audio": "Áudio",
    "video": "Vídeo",
    "document": "Documento",
    "text": "Arquivo",
    "file": "Arquivo",
}


def session_media_dir(thread_id: str) -> Path | None:
    """Pasta de mídia da sessão (a mesma lida por ``analyze_video``)."""
    if not _SAFE_THREAD.fullmatch(thread_id) or ".." in thread_id:
        return None
    from backend.settings import settings

    return settings.vectora_home / "artifacts" / thread_id / "media"


def _write_exclusive(directory: Path, filename: str, payload: bytes) -> Path:
    """Grava ``payload`` sem seguir symlinks e sem sobrescrever."""
    directory.mkdir(parents=True, exist_ok=True)
    if directory.is_symlink() or not directory.is_dir():
        raise OSError("diretório de mídia inválido")
    target = directory / filename
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as output:
            fd = -1
            output.write(payload)
    finally:
        if fd >= 0:
            os.close(fd)
    return target


def persist_media(
    payload: bytes,
    info: MediaInfo,
    *,
    thread_id: str,
    user_id: str,
    workspace_id: str,
) -> tuple[Path, str, str] | None:
    """Salva o anexo na mídia da sessão e registra o asset.

    Devolve ``(caminho, asset_id, sha256)`` ou ``None`` quando não é
    possível salvar; a falha nunca derruba o turno.
    """
    directory = session_media_dir(thread_id)
    if directory is None:
        return None
    from backend.services.assets import asset_store

    extension = EXTENSION_BY_MIME.get(info.mime_type, ".bin")
    path: Path | None = None
    try:
        path = _write_exclusive(directory, f"{uuid.uuid4().hex}{extension}", payload)
        asset = asset_store.create(
            path=path,
            owner_id=user_id,
            workspace_id=workspace_id,
            thread_id=thread_id,
            mime_type=info.mime_type,
            source="chat_attachment",
        )
    except Exception:
        logger.exception("chat: falha ao salvar anexo (%s)", info.mime_type)
        if path is not None:
            with contextlib.suppress(OSError):
                path.unlink()
        return None
    return path, asset.id, asset.sha256


def _extract_document_text(path: Path, mime_type: str) -> str:
    """Texto de PDF/DOCX/XLSX com os extratores do Context Graph."""
    from backend.context_graph import detect

    extractors = {
        "application/pdf": detect.extract_pdf_text,
        DOCX_MIME: detect.docx_to_markdown,
        XLSX_MIME: detect.xlsx_to_markdown,
    }
    extractor = extractors.get(mime_type)
    if extractor is None:
        return ""
    try:
        return extractor(path).strip()
    except Exception:
        logger.exception("chat: falha ao extrair texto de %s", path.name)
        return ""


def _header(kind: MediaKind, name: str, mime_type: str, size: int) -> str:
    return f"\n[{_LABEL_BY_KIND[kind]}: {name} — {mime_type}, {size} bytes]"


def _reference_text(
    kind: MediaKind, name: str, mime_type: str, size: int, path: Path | None
) -> str:
    header = _header(kind, name, mime_type, size)
    if path is None:
        return f"{header}\nO arquivo não pôde ser salvo nesta sessão."
    hint = ""
    if kind == "video":
        hint = " Use `analyze_video` com este caminho para perguntar sobre o vídeo."
    elif kind == "audio":
        hint = " Use `audio_transcribe` com este caminho para transcrever."
    return f"{header}\nArquivo salvo em: {path}.{hint}"


def text_attachment_block(
    payload: bytes, info: MediaInfo, name: str, size: int
) -> ContentBlock:
    """Arquivo de texto/código vira bloco com o conteúdo em cerca de código."""
    decoded = payload.decode("utf-8", errors="replace")
    lang = _LANG_BY_EXTENSION.get(Path(name).suffix.lower(), "")
    body = (
        f"\n[Arquivo: {name}]\n```{lang}\n{decoded}\n```"
        if lang
        else f"\n[Arquivo: {name}]\n{decoded}"
    )
    return ContentBlock(
        kind="media",
        media_kind="text",
        text=body,
        attachment_name=name,
        attachment_mime_type=info.mime_type,
        attachment_size_bytes=size,
    )


async def build_media_block(
    payload: bytes,
    info: MediaInfo,
    *,
    name: str,
    thread_id: str,
    user_id: str,
    workspace_id: str,
    transcribe: Callable[[], Awaitable[str]] | None = None,
) -> ContentBlock:
    """Monta o bloco de um anexo de áudio, vídeo, documento ou arquivo."""
    size = len(payload)
    if info.kind == "text":
        return text_attachment_block(payload, info, name, size)

    saved = persist_media(
        payload,
        info,
        thread_id=thread_id,
        user_id=user_id,
        workspace_id=workspace_id,
    )
    path, asset_id, sha256 = saved if saved is not None else (None, None, None)

    if info.kind == "audio" and transcribe is not None:
        text = await transcribe()
        if path is not None:
            text = f"{text}\n(Áudio original salvo em: {path})"
    elif info.kind == "document" and path is not None:
        extracted = _extract_document_text(path, info.mime_type)
        if extracted:
            truncated = len(extracted) > MAX_EXTRACTED_CHARS
            text = (
                f"{_header('document', name, info.mime_type, size)}\n"
                f"{extracted[:MAX_EXTRACTED_CHARS]}"
            )
            if truncated:
                text += "\n[texto do documento truncado]"
            text += f"\n(Original salvo em: {path})"
        else:
            text = _reference_text("document", name, info.mime_type, size, path)
            text += "\nNão foi possível extrair texto deste documento."
    else:
        text = _reference_text(info.kind, name, info.mime_type, size, path)

    return ContentBlock(
        kind="media",
        media_kind=info.kind,
        text=text,
        asset_id=asset_id,
        media_sha256=sha256,
        attachment_name=name,
        attachment_mime_type=info.mime_type,
        attachment_size_bytes=size,
    )
