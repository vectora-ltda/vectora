"""Testes de POST /vectora.chat.v1.ChatService/TranscribeAudio.

Cobre: ditado de voz gravado no cliente (MediaRecorder) — fallback usado
quando a Web Speech API do browser não está disponível (Electron/Chromium).
"""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from backend.api.handlers.chat import transcribe_audio_endpoint
from backend.api.schemas import (
    _ATTACHMENT_MAX_SIZE_AUDIO_BYTES,
    TranscribeAudioRequest,
    _max_base64_length,
)
from backend.llm.transcription import TranscriptionError


def test_transcreve_audio_com_sucesso():
    audio_b64 = base64.b64encode(b"fake-audio-bytes").decode()
    request = TranscribeAudioRequest(
        audio_base64=audio_b64, mime_type="audio/webm", filename="ditado.webm"
    )

    async def _fake_transcribe(
        data: bytes,
        filename: str,
        mime_type: str,
        **kwargs: str,
    ) -> str:
        return "olá, isso é um teste"

    with patch("backend.llm.transcription.transcribe_audio", _fake_transcribe):
        result = asyncio.run(transcribe_audio_endpoint(request))

    assert result.text == "olá, isso é um teste"


def test_audio_base64_invalido_retorna_422():
    request = TranscribeAudioRequest(
        audio_base64="!!! não é base64 válido !!!",
        mime_type="audio/webm",
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(transcribe_audio_endpoint(request))

    assert exc.value.status_code == 422


def test_audio_base64_acima_do_limite_e_recusado_antes_do_decode():
    request = TranscribeAudioRequest(
        audio_base64="A" * (_max_base64_length(_ATTACHMENT_MAX_SIZE_AUDIO_BYTES) + 1),
        mime_type="audio/webm",
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(transcribe_audio_endpoint(request))

    assert exc.value.status_code == 413


def test_falha_na_transcricao_retorna_502():
    audio_b64 = base64.b64encode(b"fake-audio-bytes").decode()
    request = TranscribeAudioRequest(audio_base64=audio_b64, mime_type="audio/webm")

    async def _fake_transcribe(
        data: bytes,
        filename: str,
        mime_type: str,
        **kwargs: str,
    ) -> str:
        raise TranscriptionError("openai_api_key não configurada")

    with patch("backend.llm.transcription.transcribe_audio", _fake_transcribe):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(transcribe_audio_endpoint(request))

    assert exc.value.status_code == 502
