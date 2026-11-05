"""Transcrição (STT) do OpenRouter — ``POST /audio/transcriptions``.

Multipart, não JSON: o corpo carrega o arquivo de áudio. Some à cadeia de
transcrição já existente (Whisper direto → Gemini → OpenRouter) em
``backend/llm/transcription.py``.
"""

from __future__ import annotations

import logging

from backend.llm.openrouter.client import OpenRouterClient, OpenRouterResponseError

logger = logging.getLogger(__name__)


async def transcribe_bytes(
    client: OpenRouterClient,
    *,
    model: str,
    data: bytes,
    filename: str,
    mime_type: str,
    language: str | None = None,
) -> str:
    """Transcreve `data` e devolve o texto, já sem espaço nas bordas."""
    if not data:
        # Gravação que falhou devolve 0 byte; mandar assim gasta crédito pra
        # receber erro do provider.
        msg = "áudio vazio — nada a transcrever"
        raise OpenRouterResponseError(msg)

    campos: dict[str, str] = {"model": model}
    if language:
        campos["language"] = language

    resposta = await client.post_multipart(
        "/audio/transcriptions",
        files={"file": (filename, data, mime_type)},
        data=campos,
    )

    texto = str(resposta.get("text") or "").strip()
    if not texto:
        # String vazia faria o usuário achar que o áudio estava mudo, em vez
        # de saber que a transcrição falhou.
        msg = "OpenRouter devolveu /audio/transcriptions sem `text` utilizável"
        raise OpenRouterResponseError(msg)
    return texto
