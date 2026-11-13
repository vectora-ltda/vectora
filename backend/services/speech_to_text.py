"""Transcrição de fala local-first para anexos de áudio e vídeo.

Ordem dos motores:

1. **Embutido:** faster-whisper, que vem com o Vectora e roda offline. O
   modelo é multilíngue (detecta o idioma sozinho); o tamanho vem de
   ``stt_local_model`` (``tiny``, ``base``, ``small``, ``medium``,
   ``large-v3``) e o idioma opcional de ``stt_language``.
2. **Sistema:** no macOS, o reconhecimento de fala nativo da Apple
   (framework Speech, no dispositivo quando o idioma suporta), pelo helper
   ``vectora-speech`` embutido no app; depois, um whisper instalado na
   máquina, detectado no PATH ou configurado: ``whisper-cli`` do whisper.cpp
   (com um modelo ``ggml-*.bin``) ou o ``whisper`` da OpenAI.
3. **Provider remoto:** o mesmo caminho da transcrição de ditado, usado só
   quando nenhum motor local está disponível.

O áudio é convertido para WAV 16 kHz mono com o ffmpeg embutido antes dos
motores de sistema, que exigem esse formato.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

SpeechEngine = Literal["embedded", "system", "provider"]

LOCAL_MODEL_SIZES = ("tiny", "base", "small", "medium", "large-v3")
DEFAULT_LOCAL_MODEL = "base"
#: Teto de uma transcrição local (vídeos longos levam mais tempo em CPU).
LOCAL_TIMEOUT_S = 600.0

#: Pastas onde o whisper.cpp costuma guardar modelos ``ggml-*.bin``.
_GGML_MODEL_DIRS = (
    "~/.cache/whisper.cpp",
    "~/.cache/whisper",
    "~/.local/share/whisper.cpp/models",
    "~/whisper.cpp/models",
    "/opt/homebrew/share/whisper-cpp",
    "/opt/homebrew/share/whisper.cpp/models",
    "/usr/local/share/whisper-cpp",
    "/usr/share/whisper.cpp/models",
)

_LOCAL_LOCK = asyncio.Lock()


@dataclass(frozen=True, slots=True)
class Transcript:
    """Resultado de uma transcrição."""

    text: str
    language: str
    engine: SpeechEngine


@dataclass(frozen=True, slots=True)
class SystemWhisper:
    """Whisper instalado no sistema operacional."""

    kind: Literal["whisper.cpp", "openai-whisper"]
    command: str
    model: str


#: Idioma curto → locale do framework Speech da Apple (que exige locale).
_APPLE_LOCALES = {
    "pt": "pt-BR",
    "en": "en-US",
    "es": "es-ES",
    "fr": "fr-FR",
    "de": "de-DE",
    "it": "it-IT",
    "ja": "ja-JP",
    "ko": "ko-KR",
    "zh": "zh-CN",
    "ru": "ru-RU",
    "nl": "nl-NL",
    "pl": "pl-PL",
    "tr": "tr-TR",
    "ar": "ar-SA",
    "hi": "hi-IN",
}


class SpeechToTextError(RuntimeError):
    """Nenhum motor conseguiu transcrever o áudio."""


def _setting(key: str) -> str:
    try:
        from backend.workspace.runtime_settings import runtime_settings

        value = runtime_settings.get(key)
    except Exception:
        return ""
    return str(value).strip() if isinstance(value, str) else ""


def local_model_size() -> str:
    """Tamanho do modelo do faster-whisper embutido."""
    size = _setting("stt_local_model")
    return size if size in LOCAL_MODEL_SIZES else DEFAULT_LOCAL_MODEL


def cached_local_models() -> dict[str, bool]:
    """Quais tamanhos do faster-whisper já estão baixados no cache do Vectora."""
    from backend.settings import settings

    root = settings.vectora_home / "models" / "whisper"
    cached: dict[str, bool] = {}
    for size in LOCAL_MODEL_SIZES:
        snapshots = root / f"models--Systran--faster-whisper-{size}" / "snapshots"
        cached[size] = snapshots.is_dir() and any(
            (snapshot / "model.bin").is_file() for snapshot in snapshots.iterdir()
        )
    return cached


def preferred_language() -> str:
    """Idioma fixo da transcrição; vazio detecta automaticamente."""
    return _setting("stt_language")


def embedded_available() -> bool:
    import importlib.util

    return importlib.util.find_spec("faster_whisper") is not None


def _find_ggml_model() -> str:
    configured = _setting("stt_system_model")
    if configured and Path(configured).expanduser().is_file():
        return str(Path(configured).expanduser())
    preference = [f"ggml-{local_model_size()}.bin", "ggml-base.bin", "ggml-small.bin"]
    for directory in _GGML_MODEL_DIRS:
        root = Path(directory).expanduser()
        if not root.is_dir():
            continue
        for name in preference:
            if (root / name).is_file():
                return str(root / name)
        candidates = sorted(root.glob("ggml-*.bin"))
        if candidates:
            return str(candidates[0])
    return ""


def detect_system_whisper() -> SystemWhisper | None:
    """Whisper do sistema: whisper.cpp (com modelo) ou openai-whisper."""
    configured = _setting("stt_system_command")
    candidates = [configured] if configured else []
    candidates += ["whisper-cli", "whisper-cpp", "whisper"]
    for candidate in candidates:
        command = shutil.which(str(Path(candidate).expanduser()))
        if command is None:
            continue
        name = Path(command).name.lower()
        if name.startswith("whisper-c"):
            model = _find_ggml_model()
            if model:
                return SystemWhisper("whisper.cpp", command, model)
            continue
        return SystemWhisper("openai-whisper", command, local_model_size())
    return None


def resolve_apple_speech() -> str | None:
    """Helper ``vectora-speech`` (framework Speech) — só no macOS.

    Ordem: bundle do app (pasta ``speech/``) → ``resources/`` da árvore-fonte
    (compilado por ``scons macos-speech``).
    """
    import sys

    from backend.services import ffmpeg_binary

    if sys.platform != "darwin":
        return None
    for base in ffmpeg_binary._frozen_bundle_bases():
        candidate = base / "speech" / "vectora-speech"
        if candidate.is_file():
            return str(candidate)
    candidate = ffmpeg_binary._resources_dir() / "vectora-speech"
    return str(candidate) if candidate.is_file() else None


def apple_locale(language: str) -> str:
    """Locale para o framework Speech; vazio usa o idioma do sistema."""
    if not language:
        return ""
    if "-" in language or "_" in language:
        return language.replace("_", "-")
    return _APPLE_LOCALES.get(language.lower(), language)


#: Idiomas oferecidos na interface (código ISO do Whisper). O Whisper aceita
#: outros; qualquer código válido pode ser gravado pela API.
SELECTABLE_LANGUAGES = (
    "pt",
    "en",
    "es",
    "fr",
    "de",
    "it",
    "ja",
    "ko",
    "zh",
    "ru",
    "nl",
    "pl",
    "tr",
    "ar",
    "hi",
)

_LANGUAGE_PATTERN = re.compile(r"^[a-z]{2,3}([-_][A-Za-z]{2,4})?$")


def validate_language(language: str) -> str:
    """Idioma fixo da transcrição: vazio (automático) ou código ISO."""
    value = language.strip()
    if value and not _LANGUAGE_PATTERN.match(value):
        raise ValueError("idioma inválido; use um código como pt, en ou pt-BR")
    return value


def validate_system_command(command: str) -> str:
    """Whisper do sistema: caminho ou nome de um executável ``whisper*``."""
    value = command.strip()
    if not value:
        return ""
    resolved = shutil.which(str(Path(value).expanduser()))
    if resolved is None or not Path(resolved).name.lower().startswith("whisper"):
        raise ValueError(
            "comando inválido; informe um executável whisper-cli ou whisper"
        )
    return value


def validate_system_model(model: str) -> str:
    """Modelo ggml do whisper.cpp: arquivo ``.bin`` existente."""
    value = model.strip()
    if not value:
        return ""
    path = Path(value).expanduser()
    if not path.is_file() or path.suffix.lower() != ".bin":
        raise ValueError("modelo inválido; informe um arquivo ggml-*.bin existente")
    return value


def stt_settings() -> dict[str, object]:
    """Configuração efetiva, motores detectados e modelos em cache."""
    return {
        "model": local_model_size(),
        "language": preferred_language(),
        "system_command": _setting("stt_system_command"),
        "system_model": _setting("stt_system_model"),
        "models": [
            {"id": size, "cached": cached}
            for size, cached in cached_local_models().items()
        ],
        "languages": list(SELECTABLE_LANGUAGES),
        "engines": available_engines(),
    }


def available_engines() -> dict[str, object]:
    """Motores locais disponíveis, para diagnóstico e interface."""
    system = detect_system_whisper()
    return {
        "embedded": embedded_available(),
        "embedded_model": local_model_size(),
        "apple_speech": resolve_apple_speech() is not None,
        "system": system is not None,
        "system_kind": system.kind if system else "",
        "language": preferred_language() or "auto",
    }


async def _run(*args: str, limit_s: float) -> tuple[int, str, str]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), limit_s)
    except TimeoutError:
        process.kill()
        await process.wait()
        return -1, "", "timeout"
    return (
        process.returncode or 0,
        stdout.decode("utf-8", errors="replace"),
        stderr.decode("utf-8", errors="replace"),
    )


async def to_wav16k(source: Path, target: Path) -> bool:
    """Converte qualquer áudio ou vídeo para WAV 16 kHz mono (ffmpeg embutido)."""
    from backend.services.ffmpeg_binary import resolve_ffmpeg

    ffmpeg = resolve_ffmpeg()
    if ffmpeg is None:
        return False
    code, _out, _err = await _run(
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(target),
        limit_s=LOCAL_TIMEOUT_S,
    )
    return code == 0 and target.is_file() and target.stat().st_size > 44


async def has_audio_stream(path: Path) -> bool:
    """O arquivo tem faixa de áudio? (ffprobe embutido)."""
    from backend.services.ffmpeg_binary import resolve_ffprobe

    ffprobe = resolve_ffprobe()
    if ffprobe is None:
        return False
    code, out, _err = await _run(
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        str(path),
        limit_s=60,
    )
    return code == 0 and bool(out.strip())


async def _transcribe_embedded(path: Path, language: str) -> Transcript:
    from backend.tools.media_native import _transcribe_local_sync

    result = await asyncio.wait_for(
        asyncio.to_thread(_transcribe_local_sync, str(path), language or None),
        timeout=LOCAL_TIMEOUT_S,
    )
    return Transcript(
        text=str(result.get("text", "")).strip(),
        language=str(result.get("language") or language or ""),
        engine="embedded",
    )


async def _transcribe_system(
    whisper: SystemWhisper, path: Path, language: str, workdir: Path
) -> Transcript:
    wav = workdir / "audio.wav"
    if not await to_wav16k(path, wav):
        raise SpeechToTextError("ffmpeg não conseguiu preparar o áudio")
    if whisper.kind == "whisper.cpp":
        prefix = workdir / "transcript"
        code, _out, err = await _run(
            whisper.command,
            "-m",
            whisper.model,
            "-f",
            str(wav),
            "-l",
            language or "auto",
            "-otxt",
            "-of",
            str(prefix),
            "-np",
            limit_s=LOCAL_TIMEOUT_S,
        )
        output = prefix.with_suffix(".txt")
    else:
        args = [
            whisper.command,
            str(wav),
            "--model",
            whisper.model,
            "--output_format",
            "txt",
            "--output_dir",
            str(workdir),
            "--fp16",
            "False",
        ]
        if language:
            args += ["--language", language]
        code, _out, err = await _run(*args, limit_s=LOCAL_TIMEOUT_S)
        output = workdir / "audio.txt"
    if code != 0 or not output.is_file():
        raise SpeechToTextError(f"whisper do sistema falhou: {err.strip()[:200]}")
    return Transcript(
        text=output.read_text(encoding="utf-8", errors="replace").strip(),
        language=language,
        engine="system",
    )


async def _transcribe_apple(
    helper: str, path: Path, language: str, workdir: Path
) -> Transcript:
    """Transcreve com o reconhecimento de fala nativo do macOS."""
    import json

    wav = workdir / "audio.wav"
    if not await to_wav16k(path, wav):
        raise SpeechToTextError("ffmpeg não conseguiu preparar o áudio")
    args = [helper, "transcribe", str(wav)]
    locale = apple_locale(language)
    if locale:
        args += ["--locale", locale]
    _code, out, err = await _run(*args, limit_s=LOCAL_TIMEOUT_S)
    try:
        result = json.loads(out.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise SpeechToTextError(
            f"reconhecimento de fala do macOS sem resposta: {err.strip()[:200]}"
        ) from exc
    if not result.get("ok"):
        raise SpeechToTextError(
            f"reconhecimento de fala do macOS: {result.get('error') or 'falhou'}"
        )
    return Transcript(
        text=str(result.get("text") or "").strip(),
        language=str(result.get("locale") or locale or language),
        engine="system",
    )


async def transcribe_local(path: Path, language: str = "") -> Transcript:
    """Transcreve com o motor embutido; sem ele, com o whisper do sistema."""
    language = language or preferred_language()
    errors: list[str] = []
    async with _LOCAL_LOCK:
        if embedded_available():
            try:
                transcript = await _transcribe_embedded(path, language)
                if transcript.text:
                    return transcript
                errors.append("motor embutido não retornou texto")
            except Exception as exc:
                logger.exception("speech_to_text: motor embutido falhou")
                errors.append(f"motor embutido: {exc}")
        apple = resolve_apple_speech()
        if apple is not None:
            with tempfile.TemporaryDirectory(prefix="vectora-stt-") as workdir:
                try:
                    transcript = await _transcribe_apple(
                        apple, path, language, Path(workdir)
                    )
                    if transcript.text:
                        return transcript
                    errors.append("reconhecimento de fala do macOS não retornou texto")
                except Exception as exc:
                    logger.exception("speech_to_text: reconhecimento do macOS falhou")
                    errors.append(str(exc))
        system = detect_system_whisper()
        if system is not None:
            with tempfile.TemporaryDirectory(prefix="vectora-stt-") as workdir:
                try:
                    transcript = await _transcribe_system(
                        system, path, language, Path(workdir)
                    )
                    if transcript.text:
                        return transcript
                    errors.append("whisper do sistema não retornou texto")
                except Exception as exc:
                    logger.exception("speech_to_text: whisper do sistema falhou")
                    errors.append(str(exc))
    raise SpeechToTextError("; ".join(errors) or "nenhum motor local disponível")


async def transcribe_bytes(
    data: bytes,
    filename: str,
    mime_type: str,
    *,
    language: str = "",
) -> Transcript:
    """Transcreve bytes local-first; sem motor local, usa o provider ativo."""
    suffix = Path(filename).suffix.lower() or ".bin"
    with tempfile.TemporaryDirectory(prefix="vectora-stt-") as workdir:
        source = Path(workdir) / f"input{suffix}"
        source.write_bytes(data)
        with contextlib.suppress(OSError):
            source.chmod(0o600)
        try:
            return await transcribe_local(source, language)
        except SpeechToTextError as local_error:
            local_reason = str(local_error)

    from backend.llm.transcription import TranscriptionError, transcribe_audio
    from backend.workspace.runtime_settings import runtime_settings

    try:
        text = await transcribe_audio(
            data,
            filename,
            mime_type,
            provider=runtime_settings.active_provider,
            model=runtime_settings.active_model,
            language=language or preferred_language(),
        )
    except TranscriptionError as exc:
        raise SpeechToTextError(
            f"sem motor local ({local_reason}) e provider indisponível ({exc})"
        ) from exc
    return Transcript(text=text.strip(), language=language, engine="provider")
