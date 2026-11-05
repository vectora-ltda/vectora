"""YouTube (e outras plataformas suportadas pelo yt-dlp): transcrição
primeiro, sem baixar mídia nenhuma quando possível.

Fluxo: legendas PÚBLICAS do YouTube via API própria (sem baixar áudio/
vídeo — caminho leve e defensável, mesmo padrão que Gemini/Hermes usam por
padrão). Só cai pra baixar áudio (yt-dlp, sem vídeo, sem re-encode — não
depende de ffmpeg vendorizado) + transcrição remota
(`backend/llm/transcription.py`) quando o vídeo não tem legendas.

Baixar VÍDEO do YouTube sem permissão viola os Termos de Uso (não é crime,
mas pode gerar suspensão de conta/ação civil por quebra de contrato) — por
isso o fallback de transcrição baixa só o stream de ÁUDIO, nunca o vídeo.
A única exceção deliberada é `youtube_frame_at`: baixa um TRECHO CURTO
(poucos segundos, `--download-sections`) em torno de um timestamp
específico que o agente já decidiu (via a transcrição) que precisa de
contexto visual — nunca o vídeo inteiro.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Any

from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)

_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/)([\w-]{11})")
_BARE_ID_RE = re.compile(r"^[\w-]{11}$")

#: Ordem de preferência quando o chamador não pede um idioma específico.
_DEFAULT_LANGUAGES = ["pt", "pt-BR", "en", "es"]

#: mime_type inferido da extensão real do stream audio-only que o yt-dlp
#: escolher (webm/opus e m4a/aac são os formatos mais comuns).
_MIME_BY_EXT = {
    "webm": "audio/webm",
    "m4a": "audio/mp4",
    "mp3": "audio/mpeg",
    "opus": "audio/opus",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
}
_SUFFIX_BY_MIME = {
    "audio/mp4": "m4a",
    "audio/opus": "opus",
}


def _audio_suffix(mime_type: str) -> str:
    """Return a filename suffix accepted by the shared STT validator."""
    normalized = mime_type.lower().split(";", 1)[0]
    return _SUFFIX_BY_MIME.get(normalized, normalized.split("/", 1)[-1])


def _extract_video_id(url: str) -> str | None:
    """Aceita URL completa (com `v=`, `youtu.be/`, `/shorts/`, `/embed/`)
    ou o ID de 11 caracteres cru — nunca lança, devolve `None` pra input
    que não é nenhum dos dois."""
    match = _VIDEO_ID_RE.search(url)
    if match:
        return match.group(1)
    candidate = url.strip()
    if _BARE_ID_RE.fullmatch(candidate):
        return candidate
    return None


def _is_supported_media_url(url: str) -> bool:
    """Confirma OFFLINE (sem tocar rede) que `url` bate com algum extrator
    real do yt-dlp — não só YouTube. Usado por `youtube_frame_at`, que
    (ao contrário de `get_transcript`) não depende de `_extract_video_id`
    pra funcionar: `_download_clip_sync` recebe a `url` original e já
    funciona com qualquer plataforma suportada. O extrator `generic`
    (fallback de última instância do yt-dlp, bate com qualquer http(s))
    não conta — rejeitaria URL inválida só depois de tentar baixar."""
    import yt_dlp.extractor as ie_module

    return any(
        cls.suitable(url) and cls.IE_NAME != "generic"
        for cls in ie_module.gen_extractor_classes()
    )


def _format_transcript(entries: list[dict[str, Any]]) -> str:
    """`[MM:SS] texto` por linha — mesmo formato que o Gemini usa pra
    transcrição de vídeo, familiar pro modelo interpretar timestamps."""
    lines: list[str] = []
    for entry in entries:
        start = int(entry.get("start", 0) or 0)
        minutes, seconds = divmod(start, 60)
        text = str(entry.get("text", "")).strip()
        if text:
            lines.append(f"[{minutes:02d}:{seconds:02d}] {text}")
    return "\n".join(lines)


def _fetch_captions_sync(video_id: str, languages: list[str]) -> list[dict[str, Any]]:
    """Síncrono de propósito (roda em thread via `asyncio.to_thread`) — a
    lib usa `requests` por baixo, sem variante async."""
    from youtube_transcript_api import YouTubeTranscriptApi

    api = YouTubeTranscriptApi()
    fetched = api.fetch(video_id, languages=languages)
    return fetched.to_raw_data()


def _download_audio_sync(url: str) -> tuple[bytes, str]:
    """Baixa só o STREAM DE ÁUDIO (nunca o vídeo) via yt-dlp, sem
    postprocessador de re-encode — evita depender de um binário ffmpeg
    vendorizado (fora do escopo desta tool). Devolve os bytes crus e o
    mime_type inferido da extensão real do stream escolhido pelo yt-dlp
    (webm/opus e m4a/aac são os formatos audio-only mais comuns)."""
    import yt_dlp

    with tempfile.TemporaryDirectory() as tmpdir:
        outtmpl = str(Path(tmpdir) / "audio.%(ext)s")
        opts = {
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])

        files = [p for p in Path(tmpdir).iterdir() if p.is_file()]
        if not files:
            raise RuntimeError("yt-dlp não produziu nenhum arquivo de áudio")
        audio_path = files[0]
        ext = audio_path.suffix.lstrip(".").lower()
        mime_type = _MIME_BY_EXT.get(ext, "application/octet-stream")
        return audio_path.read_bytes(), mime_type


@vtool(
    extras=ToolExtras(
        render_hint="json",
        category="web",
        destructive=False,
        icon="globe",
    )
)
async def get_transcript(url: str, language: str = "") -> str:
    """Obtém a transcrição de um vídeo do YouTube (ou de qualquer
    plataforma suportada pelo yt-dlp — Vimeo, Twitter/X, TikTok, etc.),
    com timestamps `[MM:SS]`.

    Caminho leve primeiro: lê as legendas PÚBLICAS do vídeo — sem baixar
    áudio nem vídeo nenhum. Só baixa o stream de ÁUDIO (nunca vídeo) e
    transcreve via LLM quando o vídeo não tem legendas — mais lento, e usa
    a chave de LLM já configurada (`transcribe_audio`).

    Args:
        url: URL do vídeo, ou o ID de 11 caracteres do YouTube.
        language: Idioma preferido da legenda (ex: "pt", "en"). Vazio tenta
            português e inglês antes de espanhol.

    Returns:
        JSON com `transcript` (texto com timestamps), `source`
        ("captions" ou "audio_fallback") e `video_id`, ou `error`.
    """
    video_id = _extract_video_id(url)
    if not video_id:
        return json.dumps(
            {"error": f"não reconheci um ID de vídeo do YouTube em: {url}"},
            ensure_ascii=False,
        )

    languages = [language] if language else _DEFAULT_LANGUAGES
    try:
        entries = await asyncio.to_thread(_fetch_captions_sync, video_id, languages)
        transcript = _format_transcript(entries)
        if transcript:
            return json.dumps(
                {
                    "transcript": transcript,
                    "source": "captions",
                    "video_id": video_id,
                },
                ensure_ascii=False,
            )
    except Exception as exc:
        logger.info(
            "get_transcript: sem legendas públicas, tentando fallback de áudio",
            extra={"video_id": video_id, "error": str(exc)},
        )

    try:
        from backend.llm.transcription import transcribe_audio

        audio_bytes, mime_type = await asyncio.to_thread(_download_audio_sync, url)
        ext = _audio_suffix(mime_type)
        from backend.workspace.runtime_settings import runtime_settings

        text = await transcribe_audio(
            audio_bytes,
            f"{video_id}.{ext}",
            mime_type,
            provider=runtime_settings.active_provider,
            model=runtime_settings.active_model,
            language="",
        )
        return json.dumps(
            {
                "transcript": text,
                "source": "audio_fallback",
                "video_id": video_id,
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        logger.exception(
            "get_transcript: fallback de áudio falhou", extra={"video_id": video_id}
        )
        return json.dumps(
            {"error": f"não foi possível obter transcrição: {exc}"},
            ensure_ascii=False,
        )


def _download_clip_sync(url: str, timestamp_s: float, window_s: float = 6.0) -> str:
    """Baixa um TRECHO CURTO do vídeo (nunca o vídeo inteiro) via
    `yt-dlp --download-sections`, centrado no `timestamp_s` pedido — a
    única exceção deliberada à regra de "nunca baixar vídeo" deste módulo
    (ver docstring do módulo). Devolve o path do clipe num diretório
    temporário — quem chama é responsável por limpar depois de usar."""
    import yt_dlp

    start = max(0.0, timestamp_s - window_s / 2)
    end = start + window_s

    tmpdir = tempfile.mkdtemp()
    outtmpl = str(Path(tmpdir) / "clip.%(ext)s")
    opts = {
        "format": "bestvideo[ext=mp4]/best[ext=mp4]/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "download_ranges": yt_dlp.utils.download_range_func(None, [(start, end)]),
        "force_keyframes_at_cuts": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([url])

    files = [p for p in Path(tmpdir).iterdir() if p.is_file()]
    if not files:
        raise RuntimeError("yt-dlp não produziu nenhum clipe de vídeo")
    return str(files[0])


@vtool(
    extras=ToolExtras(
        render_hint="image_preview",
        category="web",
        destructive=False,
        icon="globe",
    )
)
async def youtube_frame_at(ctx: ToolContext, url: str, timestamp_s: float) -> str:
    """Extrai um frame (print) de um momento específico de um vídeo —
    YouTube ou qualquer outra plataforma suportada pelo yt-dlp (Vimeo,
    Twitter/X, TikTok, etc.) — depois que `get_transcript` já identificou,
    pelo texto, que aquele momento precisa de contexto visual. Baixa só um
    TRECHO CURTO em torno do timestamp (nunca o vídeo inteiro), reusando o
    mesmo ffmpeg embutido de `probe_media`/`extract_frame`
    (`backend/tools/media_native.py`).

    Args:
        url: URL do vídeo.
        timestamp_s: Segundo exato do frame desejado.

    Returns:
        JSON com `url` (servível — aparece inline no chat) e `path`, ou
        `error`.
    """
    if timestamp_s < 0:
        return json.dumps({"error": "timestamp_s não pode ser negativo"})

    # Ao contrário de `get_transcript` (que depende do ID pra chamar a API
    # de captions, exclusiva do YouTube), esta tool não usa `video_id` em
    # nada além de log — `_download_clip_sync` recebe a `url` original e
    # já funciona com qualquer extrator do yt-dlp. Exigir o regex do
    # YouTube aqui bloquearia Vimeo/Twitter/TikTok/etc. sem motivo.
    video_id = _extract_video_id(url)
    if not _is_supported_media_url(url):
        return json.dumps(
            {"error": f"URL não reconhecida por nenhuma plataforma suportada: {url}"},
            ensure_ascii=False,
        )

    from backend.services.ffmpeg_binary import resolve_ffmpeg
    from backend.tools.media import _media_url, _persist
    from backend.tools.media_native import extract_frame_to

    ffmpeg = resolve_ffmpeg()
    if not ffmpeg:
        return json.dumps(
            {"error": "ffmpeg não disponível — instale ffmpeg ou rode `scons ffmpeg`"}
        )

    try:
        clip_path = await asyncio.to_thread(_download_clip_sync, url, timestamp_s)
    except Exception as exc:
        logger.exception(
            "youtube_frame_at: falha ao baixar trecho do vídeo",
            extra={"video_id": video_id},
        )
        return json.dumps(
            {"error": f"falha ao baixar trecho do vídeo: {exc}"}, ensure_ascii=False
        )

    try:
        # O clipe já começa `window_s/2` segundos ANTES do timestamp pedido
        # (ver `_download_clip_sync`) — o frame fica sempre no meio dele,
        # exceto perto do início do vídeo (clipe mais curto, `start=0`).
        offset_in_clip = min(timestamp_s, 3.0)
        with tempfile.TemporaryDirectory() as out_tmpdir:
            frame_tmp = str(Path(out_tmpdir) / "frame.png")
            ok, err_out = await extract_frame_to(
                ffmpeg, clip_path, offset_in_clip, frame_tmp
            )
            if not ok:
                return json.dumps(
                    {"error": f"ffmpeg falhou ao extrair frame: {err_out[:500]}"}
                )
            frame_bytes = Path(frame_tmp).read_bytes()
    finally:
        Path(clip_path).unlink(missing_ok=True)

    session_id = ctx.thread_id
    path = await asyncio.to_thread(_persist, session_id, frame_bytes, ".png")
    return json.dumps(
        {"path": str(path), "url": _media_url(session_id, path), "video_id": video_id},
        ensure_ascii=False,
    )
