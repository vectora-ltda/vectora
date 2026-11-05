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

from backend.api.handlers.chat import transcribe_audio_endpoint, transcription_readiness
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


def test_modo_local_nao_chama_provider_remoto():
    audio_b64 = base64.b64encode(b"fake-audio-bytes").decode()
    request = TranscribeAudioRequest(
        audio_base64=audio_b64,
        mime_type="audio/webm",
        filename="ditado.webm",
        transcription_mode="local",
    )

    async def _fake_local(
        data: bytes, filename: str, mime_type: str, *, language: str
    ) -> str:
        assert data == b"fake-audio-bytes"
        assert filename == "ditado.webm"
        assert mime_type == "audio/webm"
        assert language == ""
        return "transcrição local"

    with (
        patch("backend.llm.transcription.transcribe_audio_local", _fake_local),
        patch("backend.llm.transcription.transcribe_audio") as remote,
    ):
        result = asyncio.run(transcribe_audio_endpoint(request))

    assert result.text == "transcrição local"
    remote.assert_not_called()


def test_readiness_local_expoe_dependencia_e_cache_sem_tocar_modelo(monkeypatch):
    readiness = {
        "mode": "local",
        "dependency_available": True,
        "model_cached": False,
        "ready": False,
        "diagnostic": "modelo base ainda não baixado",
    }
    monkeypatch.setattr(
        "backend.llm.transcription.local_transcription_readiness",
        lambda: readiness,
    )

    result = asyncio.run(transcription_readiness())

    assert result == readiness


def test_readiness_probe_usa_caminho_explicito(monkeypatch):
    readiness = {
        "mode": "local",
        "dependency_available": True,
        "model_cached": True,
        "runtime_verified": True,
        "probe_attempted": True,
        "probe_in_progress": False,
        "ready": True,
        "diagnostic": "modelo carregado e inferência local concluída",
    }
    monkeypatch.setattr(
        "backend.llm.transcription.probe_local_transcription_readiness",
        lambda: _async_value(readiness),
    )

    result = asyncio.run(transcription_readiness(probe=True))

    assert result == readiness


async def _async_value(value):
    return value


def test_local_transcription_readiness_distingue_runtime_de_modelo_cacheado(
    tmp_path, monkeypatch
):
    from backend.llm.transcription import local_transcription_readiness
    from backend.settings import settings

    monkeypatch.setattr(settings, "vectora_home", tmp_path)
    monkeypatch.setattr(
        "backend.llm.transcription.importlib.util.find_spec",
        lambda _name: object(),
    )

    not_cached = local_transcription_readiness()
    assert not_cached["dependency_available"] is True
    assert not_cached["model_cached"] is False
    assert not_cached["ready"] is False

    snapshot = (
        tmp_path
        / "models"
        / "whisper"
        / "models--Systran--faster-whisper-base"
        / "snapshots"
        / "snapshot-id"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").write_bytes(b"weights")
    cached = local_transcription_readiness()
    assert cached["dependency_available"] is True
    assert cached["model_cached"] is True
    assert cached["ready"] is False
    assert cached["runtime_verified"] is False
    assert cached["probe_attempted"] is False


def test_local_transcription_probe_runs_cached_model_without_network(
    tmp_path, monkeypatch
):
    import sys
    import types

    from backend.llm.transcription import _probe_local_transcription_sync
    from backend.settings import settings

    monkeypatch.setattr(settings, "vectora_home", tmp_path)
    monkeypatch.setattr(
        "backend.llm.transcription.importlib.util.find_spec",
        lambda _name: object(),
    )
    snapshot = (
        tmp_path
        / "models"
        / "whisper"
        / "models--Systran--faster-whisper-base"
        / "snapshots"
        / "snapshot-id"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").write_bytes(b"weights")
    calls: list[dict[str, object]] = []

    class FakeWhisperModel:
        def __init__(self, model_path: str, **kwargs: object) -> None:
            calls.append({"model_path": model_path, **kwargs})

        def transcribe(self, audio: object, **kwargs: object):
            calls.append({"audio": audio, **kwargs})
            return iter(()), object()

    monkeypatch.setitem(
        sys.modules,
        "faster_whisper",
        types.SimpleNamespace(WhisperModel=FakeWhisperModel),
    )

    assert _probe_local_transcription_sync() == "ready"
    assert calls[0]["local_files_only"] is True
    assert calls[0]["model_path"] == str(snapshot)
    assert calls[1]["vad_filter"] is False


def test_local_transcription_probe_does_not_load_or_download_missing_model(
    tmp_path, monkeypatch
):
    from backend.llm.transcription import _probe_local_transcription_sync
    from backend.settings import settings

    monkeypatch.setattr(settings, "vectora_home", tmp_path)
    monkeypatch.setattr(
        "backend.llm.transcription.importlib.util.find_spec",
        lambda _name: object(),
    )

    assert _probe_local_transcription_sync() == "model_missing"


def test_local_transcription_readiness_reports_probe_in_progress():
    from backend.llm.transcription import (
        _LOCAL_PROBE_LOCK,
        local_transcription_readiness,
    )

    assert _LOCAL_PROBE_LOCK.acquire(blocking=False)
    try:
        readiness = local_transcription_readiness()
    finally:
        _LOCAL_PROBE_LOCK.release()

    assert readiness["probe_in_progress"] is True
    assert readiness["ready"] is False


def test_local_transcription_probe_reports_timeout_without_claiming_readiness(
    tmp_path, monkeypatch
):
    import threading

    from backend.llm.transcription import probe_local_transcription_readiness
    from backend.settings import settings

    monkeypatch.setattr(settings, "vectora_home", tmp_path)
    monkeypatch.setattr(
        "backend.llm.transcription.importlib.util.find_spec",
        lambda _name: object(),
    )
    snapshot = (
        tmp_path
        / "models"
        / "whisper"
        / "models--Systran--faster-whisper-base"
        / "snapshots"
        / "snapshot-id"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").write_bytes(b"weights")
    monkeypatch.setattr("backend.llm.transcription._LOCAL_PROBE_TIMEOUT_S", 0.001)
    monkeypatch.setattr(
        "backend.llm.transcription._probe_local_transcription_sync",
        lambda: (threading.Event().wait(0.05), "ready")[1],
    )

    result = asyncio.run(probe_local_transcription_readiness())

    assert result["probe_attempted"] is True
    assert result["runtime_verified"] is False
    assert result["probe_in_progress"] is True
    assert result["ready"] is False


def test_local_transcription_readiness_fails_when_dependency_missing(
    tmp_path, monkeypatch
):
    from backend.llm.transcription import local_transcription_readiness
    from backend.settings import settings

    monkeypatch.setattr(settings, "vectora_home", tmp_path)
    monkeypatch.setattr(
        "backend.llm.transcription.importlib.util.find_spec", lambda _name: None
    )

    result = local_transcription_readiness()

    assert result["dependency_available"] is False
    assert result["ready"] is False
    assert "não está instalado" in str(result["diagnostic"])


def test_probe_without_dependency_returns_diagnostic_without_loading_model(
    tmp_path, monkeypatch
):
    from backend.llm.transcription import probe_local_transcription_readiness
    from backend.settings import settings

    monkeypatch.setattr(settings, "vectora_home", tmp_path)
    monkeypatch.setattr(
        "backend.llm.transcription.importlib.util.find_spec", lambda _name: None
    )

    result = asyncio.run(probe_local_transcription_readiness())

    assert result["probe_attempted"] is True
    assert result["ready"] is False
    assert result["diagnostic"] == "faster-whisper não está instalado"


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
