"""Endpoints de configuração da transcrição local (/admin/transcription)."""

from __future__ import annotations

import stat
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from fastapi import HTTPException

from backend.api.handlers import admin
from backend.services import speech_to_text as stt

if TYPE_CHECKING:
    from fastapi import Request


class _Request:
    """Requisição mínima: ``require_admin`` e ``_get_user`` são substituídos."""


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Troca os campos stt_* por um dicionário em memória."""
    values: dict[str, str] = {}
    monkeypatch.setattr(admin, "require_admin", lambda _user: None)
    monkeypatch.setattr(admin, "_get_user", lambda _request: object())
    monkeypatch.setattr(stt, "_setting", lambda key: values.get(key, ""))

    class _Field:
        def __init__(self, name: str) -> None:
            self.name = name

        def set(self, value: str) -> None:
            values[self.name] = value

    monkeypatch.setattr("backend.config.registry.get_field", _Field)
    return values


def _executable(path: Path) -> Path:
    path.write_text(f"#!{sys.executable}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


async def test_get_devolve_configuracao_motores_e_modelos(
    store: dict[str, str],
) -> None:
    data = await admin.get_transcription_settings(cast("Request", _Request()))
    assert data["model"] == "base"
    assert data["language"] == ""
    assert [m["id"] for m in data["models"]] == list(stt.LOCAL_MODEL_SIZES)
    assert "pt" in data["languages"]
    assert set(data["engines"]) >= {"embedded", "apple_speech", "system", "language"}


async def test_patch_grava_e_devolve_o_estado_efetivo(
    store: dict[str, str], tmp_path: Path
) -> None:
    ggml = tmp_path / "ggml-small.bin"
    ggml.write_bytes(b"x")
    cli = _executable(tmp_path / "whisper-cli")

    data = await admin.patch_transcription_settings(
        cast("Request", _Request()),
        admin.TranscriptionSettingsBody(
            model="small",
            language="pt",
            system_command=str(cli),
            system_model=str(ggml),
        ),
    )
    assert (data["model"], data["language"]) == ("small", "pt")
    assert data["system_command"] == str(cli)
    assert data["system_model"] == str(ggml)


async def test_campos_ausentes_nao_mudam_e_vazio_limpa(
    store: dict[str, str],
) -> None:
    store["stt_language"] = "en"
    await admin.patch_transcription_settings(
        cast("Request", _Request()),
        admin.TranscriptionSettingsBody(model="tiny"),
    )
    assert store == {"stt_language": "en", "stt_local_model": "tiny"}
    await admin.patch_transcription_settings(
        cast("Request", _Request()),
        admin.TranscriptionSettingsBody(language=""),
    )
    assert store["stt_language"] == ""


@pytest.mark.parametrize(
    "body",
    [
        {"model": "gigante"},
        {"language": "português!"},
        {"system_command": "/bin/ls"},
        {"system_command": "nao-existe-whisper"},
        {"system_model": "/nao/existe/ggml.bin"},
    ],
)
async def test_valores_invalidos_sao_recusados_sem_gravar(
    store: dict[str, str], body: dict[str, str]
) -> None:
    with pytest.raises(HTTPException) as error:
        await admin.patch_transcription_settings(
            cast("Request", _Request()),
            admin.TranscriptionSettingsBody(**body),
        )
    assert error.value.status_code == 422
    assert store == {}


def test_modelos_em_cache_so_com_model_bin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.settings import settings

    monkeypatch.setattr(settings, "vectora_home", tmp_path)
    snapshot = (
        tmp_path
        / "models"
        / "whisper"
        / "models--Systran--faster-whisper-small"
        / "snapshots"
        / "abc"
    )
    snapshot.mkdir(parents=True)
    assert stt.cached_local_models()["small"] is False
    (snapshot / "model.bin").write_bytes(b"x")
    cached = stt.cached_local_models()
    assert cached["small"] is True
    assert cached["base"] is False


def test_ditado_local_usa_o_idioma_configurado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.llm import transcription

    received: list[object] = []
    monkeypatch.setattr(
        stt, "_setting", lambda key: "es" if key == "stt_language" else ""
    )
    monkeypatch.setattr(
        "backend.tools.media_native._transcribe_local_sync",
        lambda path, language: received.append(language) or {"text": "hola"},
    )
    assert transcription._transcribe_audio_local_sync(b"ID3abc", "a.mp3", "") == "hola"
    assert received == ["es"]
