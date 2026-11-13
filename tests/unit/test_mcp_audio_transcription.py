"""Áudio devolvido por ferramentas MCP chega ao modelo como transcrição local."""

from __future__ import annotations

import base64
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services import speech_to_text as stt
from backend.tools import mcp
from backend.tools.mcp import _render_call_result, _transcribe_audio_blocks

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"0" * 24


def _audio(data: bytes = WAV, mime: str = "audio/wav") -> SimpleNamespace:
    return SimpleNamespace(
        type="audio", data=base64.b64encode(data).decode(), mimeType=mime
    )


def _result(*blocks: SimpleNamespace) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), isError=False)


@pytest.fixture
def local_stt(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Troca o motor local por um que devolve um texto fixo e guarda o arquivo."""
    seen: list[Path] = []

    async def fake(path: Path, language: str = "") -> stt.Transcript:
        seen.append(path)
        assert path.read_bytes() == WAV
        return stt.Transcript(text="olá do servidor", language="pt", engine="embedded")

    monkeypatch.setattr(stt, "transcribe_local", fake)
    return seen


async def test_audio_vira_transcricao_local_no_resultado(
    local_stt: list[Path],
) -> None:
    result = _result(SimpleNamespace(type="text", text="gravado"), _audio())
    transcripts = await _transcribe_audio_blocks(result)
    rendered = _render_call_result(result, None, transcripts)

    assert "gravado" in rendered
    assert "transcrição local, idioma: pt" in rendered
    assert "olá do servidor" in rendered
    assert base64.b64encode(WAV).decode() not in rendered
    assert len(local_stt) == 1
    assert not local_stt[0].exists()  # arquivo temporário removido


async def test_sem_motor_local_fica_a_nota(monkeypatch: pytest.MonkeyPatch) -> None:
    async def falha(path: Path, language: str = "") -> stt.Transcript:
        raise stt.SpeechToTextError("nenhum motor local disponível")

    monkeypatch.setattr(stt, "transcribe_local", falha)
    result = _result(_audio())
    transcripts = await _transcribe_audio_blocks(result)

    assert transcripts == {}
    rendered = _render_call_result(result, None, transcripts)
    assert "não transcrito e não enviado ao modelo" in rendered


async def test_base64_invalido_e_audio_grande_demais_sao_ignorados(
    monkeypatch: pytest.MonkeyPatch, local_stt: list[Path]
) -> None:
    monkeypatch.setattr(mcp, "_MAX_AUDIO_BYTES", 8)
    invalido = SimpleNamespace(type="audio", data="@@@", mimeType="audio/wav")
    transcripts = await _transcribe_audio_blocks(_result(invalido, _audio()))

    assert transcripts == {}
    assert local_stt == []


async def test_transcricao_longa_e_cortada(monkeypatch: pytest.MonkeyPatch) -> None:
    async def longo(path: Path, language: str = "") -> stt.Transcript:
        return stt.Transcript(text="a" * 50_000, language="", engine="embedded")

    monkeypatch.setattr(stt, "transcribe_local", longo)
    transcripts = await _transcribe_audio_blocks(_result(_audio()))

    assert len(transcripts[0]) < 12_100


async def test_resultado_sem_audio_nao_chama_o_motor(local_stt: list[Path]) -> None:
    transcripts = await _transcribe_audio_blocks(
        _result(SimpleNamespace(type="text", text="oi"))
    )
    assert transcripts == {}
    assert local_stt == []
