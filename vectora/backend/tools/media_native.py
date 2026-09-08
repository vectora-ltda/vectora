"""Análise de mídia LOCAL via ffmpeg/ffprobe embutido — sem custo de API,
sem depender de nenhum provider remoto.

Distinto de ``backend/tools/media.py`` (100% "manda pro provider"): as
tools daqui rodam inteiramente no processo local, usando o binário
resolvido por ``backend/services/ffmpeg_binary.py``. Entram em jogo quando
o usuário quer análise de mídia sem gastar tokens de API, ou quando nenhum
provider multimodal está configurado.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from uuid import uuid4

from backend.services.ffmpeg_binary import resolve_ffmpeg, resolve_ffprobe
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)

#: Teto de tempo pra qualquer chamada de ffmpeg/ffprobe — mídia gigante ou
#: um binário travado não pode segurar o agente indefinidamente.
_TIMEOUT_S = 120.0


async def _run(*args: str) -> tuple[int, str, str]:
    """``asyncio.create_subprocess_exec`` (CLAUDE.md #10 — async-first,
    nunca `subprocess.run` síncrono), com teto de tempo. Nunca lança por
    timeout — mata o processo e devolve código -1."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), _TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "", f"timeout após {_TIMEOUT_S}s"
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


def _resolve_media_path(path: str, ctx: ToolContext) -> tuple[Path | None, str]:
    """Mesma defesa anti-traversal que `file_read`/`ingest_docs` usam —
    `path` vem do modelo (tool call), não pode escapar do workspace."""
    from backend.tools.fs import _confine

    resolved, err = _confine(path, ctx)
    if resolved is None:
        return None, err
    if not resolved.is_file():
        return None, f"Error: '{path}' não é um arquivo"
    return resolved, ""


@vtool(
    extras=ToolExtras(
        render_hint="json",
        category="media",
        destructive=False,
        icon="film",
    )
)
async def probe_media(ctx: ToolContext, path: str) -> str:
    """Inspeciona um arquivo de mídia LOCAL (vídeo/áudio) via ffprobe — sem
    enviar nada pra nenhum provider. Devolve duração, codecs, resolução e
    demais metadados técnicos, base pra qualquer decisão downstream (ex.:
    "vídeo tem mais de 2h, só um trecho será analisado").

    Args:
        path: Caminho do arquivo de mídia dentro do workspace.

    Returns:
        JSON com os campos que o ffprobe reporta (`format`, `streams`), ou
        `error`.
    """
    resolved, err = _resolve_media_path(path, ctx)
    if resolved is None:
        return err

    ffprobe = resolve_ffprobe()
    if not ffprobe:
        return json.dumps(
            {"error": "ffprobe não disponível — instale ffmpeg ou rode `scons ffmpeg`"}
        )

    code, out, err_out = await _run(
        ffprobe,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(resolved),
    )
    if code != 0:
        return json.dumps({"error": f"ffprobe falhou: {err_out[:500]}"})
    try:
        return json.dumps(json.loads(out), ensure_ascii=False)
    except json.JSONDecodeError:
        return json.dumps({"error": "ffprobe devolveu saída inválida"})


@vtool(
    extras=ToolExtras(
        render_hint="image_preview",
        category="media",
        destructive=False,
        icon="film",
    )
)
async def extract_frame(ctx: ToolContext, path: str, timestamp_s: float) -> str:
    """Extrai um frame de um vídeo LOCAL num timestamp específico — "print"
    de um momento exato, sem processar o vídeo inteiro nem gastar tokens de
    API. Base do `youtube_frame_at` (extração sob demanda de vídeos do
    YouTube), mas funciona pra qualquer vídeo local.

    Args:
        path: Caminho do vídeo dentro do workspace.
        timestamp_s: Segundo exato do frame desejado.

    Returns:
        JSON com o `path` do PNG extraído, ou `error`.
    """
    resolved, err = _resolve_media_path(path, ctx)
    if resolved is None:
        return err
    if timestamp_s < 0:
        return json.dumps({"error": "timestamp_s não pode ser negativo"})

    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        return json.dumps(
            {"error": "ffmpeg não disponível — instale ffmpeg ou rode `scons ffmpeg`"}
        )

    out_dir = resolved.parent / ".vectora-frames"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{resolved.stem}-{uuid4().hex[:8]}.png"

    ok, err_out = await extract_frame_to(
        ffmpeg, str(resolved), timestamp_s, str(out_path)
    )
    if not ok:
        return json.dumps({"error": f"ffmpeg falhou ao extrair frame: {err_out[:500]}"})
    return json.dumps({"path": str(out_path)}, ensure_ascii=False)


async def extract_frame_to(
    ffmpeg: str, video_path: str, timestamp_s: float, out_path: str
) -> tuple[bool, str]:
    """Extração de frame reutilizável fora do contexto de tool — usada
    tanto pela tool `extract_frame` (vídeo do workspace) quanto por
    `backend/tools/youtube.py::youtube_frame_at` (clipe baixado sob
    demanda, sem workspace nenhum envolvido). Devolve (sucesso, stderr)."""
    code, _out, err_out = await _run(
        ffmpeg,
        "-y",
        "-ss",
        str(timestamp_s),
        "-i",
        video_path,
        "-frames:v",
        "1",
        out_path,
    )
    return code == 0 and Path(out_path).is_file(), err_out


@vtool(
    extras=ToolExtras(
        render_hint="json",
        category="media",
        destructive=False,
        icon="film",
    )
)
async def extract_audio(ctx: ToolContext, path: str) -> str:
    """Extrai a trilha de áudio de um vídeo LOCAL como WAV (PCM 16-bit) —
    prepara pra transcrição local (`transcribe_local`) ou qualquer outro
    processamento de áudio, sem gastar tokens de API.

    Args:
        path: Caminho do vídeo dentro do workspace.

    Returns:
        JSON com o `path` do WAV extraído, ou `error`.
    """
    resolved, err = _resolve_media_path(path, ctx)
    if resolved is None:
        return err

    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        return json.dumps(
            {"error": "ffmpeg não disponível — instale ffmpeg ou rode `scons ffmpeg`"}
        )

    out_dir = resolved.parent / ".vectora-audio"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{resolved.stem}-{uuid4().hex[:8]}.wav"

    code, _out, err_out = await _run(
        ffmpeg,
        "-y",
        "-i",
        str(resolved),
        "-vn",
        "-acodec",
        "pcm_s16le",
        str(out_path),
    )
    if code != 0 or not out_path.is_file():
        return json.dumps({"error": f"ffmpeg falhou ao extrair áudio: {err_out[:500]}"})
    return json.dumps({"path": str(out_path)}, ensure_ascii=False)


def _transcribe_local_sync(audio_path: str, language: str | None) -> dict:
    """Síncrono de propósito (roda em thread via `asyncio.to_thread`) —
    `faster-whisper`/CTranslate2 não tem variante async."""
    from faster_whisper import WhisperModel

    from backend.settings import settings

    model_dir = settings.vectora_home / "models" / "whisper"
    model_dir.mkdir(parents=True, exist_ok=True)
    # "base" — equilíbrio precisão/velocidade em CPU sem GPU dedicada;
    # baixado sob demanda (~150MB) na primeira transcrição, cacheado aqui.
    model = WhisperModel(
        "base", device="cpu", compute_type="int8", download_root=str(model_dir)
    )
    segments, info = model.transcribe(audio_path, language=language or None)
    texto = " ".join(seg.text.strip() for seg in segments)
    return {
        "text": texto.strip(),
        "language": info.language,
        "language_probability": info.language_probability,
    }


@vtool(
    extras=ToolExtras(
        render_hint="json",
        category="media",
        destructive=False,
        icon="film",
    )
)
async def transcribe_local(ctx: ToolContext, path: str, language: str = "") -> str:
    """Transcreve um áudio LOCAL via faster-whisper — 100% offline, sem
    enviar nada pra nenhum provider. Modelo baixado sob demanda (~150MB) na
    primeira chamada, cacheado em `~/.vectora/models/whisper/`.

    Use quando não houver provider de transcrição remoto configurado, ou
    quando o usuário pedir explicitamente transcrição local (sem gastar
    tokens de API).

    Args:
        path: Caminho do arquivo de áudio (WAV/MP3/etc) dentro do workspace.
        language: Código do idioma (ex: "pt", "en"). Vazio detecta
            automaticamente.

    Returns:
        JSON com `text` (transcrição), `language` (detectado ou informado)
        e `language_probability`, ou `error`.
    """
    resolved, err = _resolve_media_path(path, ctx)
    if resolved is None:
        return err

    try:
        result = await asyncio.to_thread(
            _transcribe_local_sync, str(resolved), language or None
        )
    except Exception as exc:
        logger.exception("transcribe_local: falha", extra={"path": path})
        return json.dumps({"error": f"falha ao transcrever: {exc}"}, ensure_ascii=False)

    return json.dumps(result, ensure_ascii=False)
