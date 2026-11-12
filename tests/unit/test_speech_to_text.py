"""Transcrição local-first: motor embutido, whisper do sistema e provider."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.services import speech_to_text as stt
from backend.services.ffmpeg_binary import resolve_ffmpeg

_ASSETS = Path(__file__).resolve().parents[1] / "assets"

requires_ffmpeg = pytest.mark.skipif(
    resolve_ffmpeg() is None, reason="ffmpeg necessário"
)


def _make_video(path: Path, *inputs: str) -> None:
    """Gera um vídeo de teste com o ffmpeg (fontes lavfi)."""
    import subprocess

    subprocess.run(  # noqa: S603
        [
            str(resolve_ffmpeg()),
            "-loglevel",
            "error",
            *inputs,
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
    )


def _fake_whisper_cpp(directory: Path) -> Path:
    """`whisper-cli` falso que grava o texto no arquivo pedido por -of."""
    script = directory / "whisper-cli"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
        "prefix = args[args.index('-of') + 1]\n"
        "lang = args[args.index('-l') + 1]\n"
        "open(prefix + '.txt', 'w').write(f'transcrito pelo sistema ({lang})')\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return script


@pytest.fixture
def no_settings(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    values: dict[str, str] = {}
    monkeypatch.setattr(stt, "_setting", lambda key: values.get(key, ""))
    return values


def test_modelo_local_padrao_e_configuravel(no_settings: dict[str, str]) -> None:
    assert stt.local_model_size() == "base"
    no_settings["stt_local_model"] = "small"
    assert stt.local_model_size() == "small"
    no_settings["stt_local_model"] = "inexistente"
    assert stt.local_model_size() == "base"


def test_detecta_whisper_cpp_com_modelo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_settings: dict[str, str]
) -> None:
    script = _fake_whisper_cpp(tmp_path)
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"x")
    monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}/usr/bin")
    no_settings["stt_system_model"] = str(model)

    detected = stt.detect_system_whisper()
    assert detected == stt.SystemWhisper("whisper.cpp", str(script), str(model))


def test_whisper_cpp_sem_modelo_nao_e_usado(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_settings: dict[str, str]
) -> None:
    _fake_whisper_cpp(tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.setattr(stt, "_GGML_MODEL_DIRS", ())
    assert stt.detect_system_whisper() is None


@requires_ffmpeg
async def test_sem_motor_embutido_usa_whisper_do_sistema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_settings: dict[str, str]
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_whisper_cpp(bin_dir)
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"x")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    no_settings["stt_system_model"] = str(model)
    no_settings["stt_language"] = "pt"
    monkeypatch.setattr(stt, "embedded_available", lambda: False)

    transcript = await stt.transcribe_local(_ASSETS / "sample.wav")
    assert transcript == stt.Transcript("transcrito pelo sistema (pt)", "pt", "system")


async def test_motor_embutido_tem_prioridade(
    monkeypatch: pytest.MonkeyPatch, no_settings: dict[str, str]
) -> None:
    monkeypatch.setattr(stt, "embedded_available", lambda: True)
    monkeypatch.setattr(
        "backend.tools.media_native._transcribe_local_sync",
        lambda path, language: {"text": "olá mundo", "language": "pt"},
    )
    monkeypatch.setattr(
        stt, "detect_system_whisper", lambda: pytest.fail("não deveria")
    )
    transcript = await stt.transcribe_local(Path("audio.wav"))
    assert transcript == stt.Transcript("olá mundo", "pt", "embedded")


async def test_sem_motor_local_cai_no_provider(no_settings: dict[str, str]) -> None:
    with (
        patch.object(
            stt, "transcribe_local", AsyncMock(side_effect=stt.SpeechToTextError("x"))
        ),
        patch(
            "backend.llm.transcription.transcribe_audio",
            AsyncMock(return_value="remoto"),
        ),
    ):
        transcript = await stt.transcribe_bytes(b"ID3abc", "a.mp3", "audio/mpeg")
    assert (transcript.text, transcript.engine) == ("remoto", "provider")


@requires_ffmpeg
async def test_audio_do_video_detectado_e_convertido(tmp_path: Path) -> None:
    video = _ASSETS / "sample.mp4"
    has_audio = await stt.has_audio_stream(video)
    wav = tmp_path / "out.wav"
    if has_audio:
        assert await stt.to_wav16k(video, wav)
        assert wav.read_bytes()[:4] == b"RIFF"
    silent = tmp_path / "silent.mp4"
    _make_video(silent, "-f", "lavfi", "-i", "color=c=blue:size=64x64:duration=1")
    assert await stt.has_audio_stream(silent) is False


@requires_ffmpeg
async def test_video_anexado_ganha_transcricao_do_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.services.assets import AssetStore
    from backend.services.chat_attachments import build_media_block
    from backend.services.media_types import MediaInfo
    from backend.settings import settings

    monkeypatch.setattr(settings, "vectora_home", tmp_path)
    monkeypatch.setattr("backend.services.assets.asset_store", AssetStore())
    video = tmp_path / "fala.mp4"
    _make_video(
        video,
        "-f",
        "lavfi",
        "-i",
        "color=c=red:size=64x64:duration=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=2",
        "-shortest",
    )
    monkeypatch.setattr(
        stt,
        "transcribe_local",
        AsyncMock(return_value=stt.Transcript("bom dia equipe", "pt", "embedded")),
    )
    block = await build_media_block(
        video.read_bytes(),
        MediaInfo(kind="video", mime_type="video/mp4"),
        name="fala.mp4",
        thread_id="t1",
        user_id="u1",
        workspace_id="w1",
    )
    assert block.text is not None
    assert "Transcrição do áudio do vídeo, idioma: pt" in block.text
    assert "bom dia equipe" in block.text
    assert block.media_preview_asset_ids


async def test_pdf_to_images_mostra_paginas_ao_modelo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    from pypdf import PdfWriter

    from backend.services.assets import AssetStore
    from backend.services.tool_media import tool_image_blocks
    from backend.settings import settings
    from backend.tools.context import ToolContext
    from backend.tools.media_native import pdf_to_images

    monkeypatch.setattr(settings, "vectora_home", tmp_path)
    monkeypatch.setattr("backend.services.assets.asset_store", AssetStore())
    writer = PdfWriter()
    for _ in range(5):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(buffer.getvalue())

    ctx = ToolContext(user_id="u1", workspace_id="w1", thread_id="t1")
    with patch("backend.tools.fs._confine", return_value=(pdf, "")):
        result = await pdf_to_images(ctx=ctx, path="doc.pdf", first_page=2, max_pages=3)

    data = json.loads(result)
    assert data["total_pages"] == 5
    assert [page["page"] for page in data["pages"]] == [2, 3, 4]
    assert len(tool_image_blocks(result)) == 3

    with patch("backend.tools.fs._confine", return_value=(pdf, "")):
        beyond = json.loads(await pdf_to_images(ctx=ctx, path="doc.pdf", first_page=9))
    assert "error" in beyond


@requires_ffmpeg
def test_whisper_embutido_recebe_audio_decodificado_pelo_ffmpeg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regressão: o faster-whisper 1.2.1 quebra com PyAV 19 ao decodificar o
    arquivo; o Vectora decodifica com o ffmpeg e entrega o array ao modelo."""
    import sys
    import types

    import numpy as np

    from backend.tools import media_native

    received: list[object] = []

    class _FakeModel:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def transcribe(self, audio: object, language: object = None):
            received.append(audio)
            segment = types.SimpleNamespace(text=" olá ")
            info = types.SimpleNamespace(language="pt", language_probability=0.9)
            return iter([segment]), info

    monkeypatch.setitem(
        sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=_FakeModel)
    )
    result = media_native._transcribe_local_sync(str(_ASSETS / "sample.wav"), None)

    assert result["text"] == "olá"
    assert isinstance(received[0], np.ndarray)
    assert received[0].dtype == np.float32


def _fake_apple_helper(directory: Path, *, ok: bool = True) -> Path:
    """`vectora-speech` falso com o protocolo JSON do helper Swift."""
    helper = directory / "vectora-speech"
    body = (
        "out = {'ok': True, 'text': 'olá do macOS', 'locale': locale or 'pt-BR', "
        "'on_device': True}"
        if ok
        else "out = {'ok': False, 'error': 'não autorizado'}"
    )
    helper.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "args = sys.argv[1:]\n"
        "assert args[0] == 'transcribe'\n"
        "locale = args[args.index('--locale') + 1] if '--locale' in args else ''\n"
        f"{body}\n"
        "print(json.dumps(out))\n"
        "sys.exit(0 if out['ok'] else 1)\n"
    )
    helper.chmod(helper.stat().st_mode | stat.S_IEXEC)
    return helper


def test_locale_da_apple_a_partir_do_idioma() -> None:
    assert stt.apple_locale("") == ""
    assert stt.apple_locale("pt") == "pt-BR"
    assert stt.apple_locale("en") == "en-US"
    assert stt.apple_locale("pt_PT") == "pt-PT"
    assert stt.apple_locale("sv") == "sv"


def test_helper_da_apple_so_no_macos(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.services import ffmpeg_binary

    helper = _fake_apple_helper(tmp_path)
    monkeypatch.setattr(ffmpeg_binary, "_resources_dir", lambda: tmp_path)
    monkeypatch.setattr(ffmpeg_binary, "_frozen_bundle_bases", list)
    monkeypatch.setattr(sys, "platform", "linux")
    assert stt.resolve_apple_speech() is None
    monkeypatch.setattr(sys, "platform", "darwin")
    assert stt.resolve_apple_speech() == str(helper)


@requires_ffmpeg
async def test_no_macos_usa_reconhecimento_nativo_antes_do_whisper_do_sistema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_settings: dict[str, str]
) -> None:
    helper = _fake_apple_helper(tmp_path)
    monkeypatch.setattr(stt, "embedded_available", lambda: False)
    monkeypatch.setattr(stt, "resolve_apple_speech", lambda: str(helper))
    monkeypatch.setattr(
        stt, "detect_system_whisper", lambda: pytest.fail("não deveria chegar aqui")
    )
    no_settings["stt_language"] = "pt"

    transcript = await stt.transcribe_local(_ASSETS / "sample.wav")
    assert transcript == stt.Transcript("olá do macOS", "pt-BR", "system")


@requires_ffmpeg
async def test_falha_do_macos_cai_no_whisper_do_sistema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_settings: dict[str, str]
) -> None:
    apple_dir = tmp_path / "apple"
    apple_dir.mkdir()
    helper = _fake_apple_helper(apple_dir, ok=False)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_whisper_cpp(bin_dir)
    model = tmp_path / "ggml-base.bin"
    model.write_bytes(b"x")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    no_settings["stt_system_model"] = str(model)
    monkeypatch.setattr(stt, "embedded_available", lambda: False)
    monkeypatch.setattr(stt, "resolve_apple_speech", lambda: str(helper))

    transcript = await stt.transcribe_local(_ASSETS / "sample.wav")
    assert transcript.text == "transcrito pelo sistema (auto)"
