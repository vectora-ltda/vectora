"""Transcrição de áudio (STT).

Usado pro ditado de voz no desktop (`TranscribeAudio` endpoint) e pra anexos
de áudio pré-gravados no chat — o ditado de voz do browser
(`use-voice-input.ts`) é 100% client-side via Web Speech API, sem chamada
nenhuma ao backend.

Tenta OpenAI Whisper primeiro (mais preciso pra transcrição pura); sem
``openai_api_key``, cai pro Gemini (audio understanding) usando a mesma
``google_api_key`` já configurada pro chat; sem nenhuma das duas, usa o
OpenRouter — que exige key **e** modelo de STT escolhido, porque sem modelo
não há o que chamar.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from backend.settings import settings

logger = logging.getLogger(__name__)

_OPENAI_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"
_OPENAI_TRANSCRIPTION_MODEL = "whisper-1"
_GEMINI_TRANSCRIPTION_MODEL = "gemini-2.5-flash"
_GEMINI_TRANSCRIPTION_PROMPT = (
    "Transcreva o áudio a seguir literalmente, sem comentários nem "
    "formatação — devolva só o texto falado."
)
_TIMEOUT_S = 60.0
#: 503 UNAVAILABLE ("high demand") do Gemini é documentado como transitório —
#: o SDK já retenta internamente (curto, via tenacity), mas o pico costuma
#: durar mais que esse retry embutido cobre. Retentamos de novo, mais espaçado.
_GEMINI_MAX_ATTEMPTS = 3
_GEMINI_RETRY_DELAY_S = 2.0


class TranscriptionError(Exception):
    """Falha ao transcrever um áudio — sem chave configurada ou erro da API."""


async def transcribe_audio(
    data: bytes,
    filename: str,
    mime_type: str,
    *,
    provider: str,
    model: str,
    language: str,
) -> str:
    """Transcreve `data` (bytes de áudio) via OpenAI Whisper ou Gemini.

    Args:
        data: Conteúdo binário do arquivo de áudio.
        filename: Nome original do arquivo (usado no multipart do Whisper).
        mime_type: Content-Type do arquivo.

    Returns:
        Texto transcrito.

    Raises:
        TranscriptionError: Sem chave configurada (nem OpenAI nem Google) ou
            erro da API.
    """
    if not provider or not model:
        raise TranscriptionError("provider e modelo são obrigatórios")
    provider_key = provider.lower().replace("_", "-")
    if provider_key in {"openai", "openai-api"} and settings.openai_api_key:
        return await _transcribe_openai(data, filename, mime_type, model, language)
    if provider_key in {"google", "google-genai", "gemini"} and settings.google_api_key:
        return await _transcribe_gemini(data, mime_type, model, language)
    if provider_key == "openrouter" and settings.openrouter_api_key:
        return await _transcribe_openrouter(data, filename, mime_type, model, language)
    raise TranscriptionError(
        f"provider de transcrição indisponível: {provider} (verifique openai_api_key, google_api_key ou openrouter_api_key)"
    )


def _openrouter_stt_model() -> str:
    """Modelo de STT escolhido pro OpenRouter — vazio desliga a capacidade.

    Key configurada sem modelo não basta: `/audio/transcriptions` exige o
    `model` no corpo, e adivinhar um aqui escolheria pelo usuário.
    """
    from backend.settings import configured_gateway_model

    return configured_gateway_model("openrouter", "stt")


async def _transcribe_openrouter(
    data: bytes, filename: str, mime_type: str, model: str, language: str
) -> str:
    from backend.llm.openrouter.client import OpenRouterClient, OpenRouterError
    from backend.llm.openrouter.stt import transcribe_bytes

    client = OpenRouterClient(api_key=settings.openrouter_api_key or "")
    try:
        return await transcribe_bytes(
            client,
            model=model,
            data=data,
            filename=filename,
            mime_type=mime_type,
            language=language or None,
        )
    except OpenRouterError as exc:
        logger.exception("transcribe_audio: falha na transcrição via OpenRouter")
        raise TranscriptionError(str(exc)) from exc
    finally:
        await client.aclose()


async def _transcribe_openai(
    data: bytes, filename: str, mime_type: str, model: str, language: str
) -> str:
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            response = await client.post(
                _OPENAI_TRANSCRIPTION_URL,
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                files={"file": (filename, data, mime_type)},
                data={"model": model, **({"language": language} if language else {})},
            )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.exception("transcribe_audio: falha na chamada à API Whisper")
        raise TranscriptionError(str(exc)) from exc

    return str(response.json().get("text", "")).strip()


async def _transcribe_gemini(
    data: bytes, mime_type: str, model: str, language: str
) -> str:
    from google import genai
    from google.genai import types
    from google.genai.errors import ServerError

    client = genai.Client(api_key=settings.google_api_key)

    last_exc: Exception = TranscriptionError("Gemini indisponível")
    for attempt in range(1, _GEMINI_MAX_ATTEMPTS + 1):
        try:
            response = await client.aio.models.generate_content(
                model=model,
                contents=[
                    _GEMINI_TRANSCRIPTION_PROMPT,
                    f"Idioma: {language}" if language else "",
                    types.Part.from_bytes(data=data, mime_type=mime_type),
                ],
            )
            return (response.text or "").strip()
        except ServerError as exc:
            last_exc = exc
            logger.warning(
                "transcribe_audio: Gemini indisponível (tentativa %d/%d): %s",
                attempt,
                _GEMINI_MAX_ATTEMPTS,
                exc,
            )
            if attempt < _GEMINI_MAX_ATTEMPTS:
                await asyncio.sleep(_GEMINI_RETRY_DELAY_S * attempt)
        except Exception as exc:
            logger.exception("transcribe_audio: falha na chamada à API Gemini")
            raise TranscriptionError(str(exc)) from exc

    logger.error(
        "transcribe_audio: Gemini indisponível após %d tentativas",
        _GEMINI_MAX_ATTEMPTS,
    )
    raise TranscriptionError(str(last_exc)) from last_exc
