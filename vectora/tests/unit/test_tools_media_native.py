"""``backend/tools/media_native.py`` — análise de mídia LOCAL via
ffmpeg/ffprobe, sem provider remoto nenhum.

Assets reais em ``tests/assets/`` (``sample.mp4``/``sample.wav``/
``sample.ogg``/``sample.mp3``/``sample.png``/``sample.jpg``/
``sample.webp``/``sample.gif``/``sample.webm``, gerados localmente via
``ffmpeg -f lavfi``) — mesma convenção de ``test_tools_rag.py`` pros
parsers de documento. Roda contra o ffmpeg de verdade (resolvido por
``ffmpeg_binary.py``, PATH do sistema em dev) — sem mock de subprocess,
prova que a integração real funciona contra a diversidade de
container/codec que o ffmpeg embutido precisa suportar na prática.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from backend.services.ffmpeg_binary import resolve_ffmpeg
from backend.tools.context import ToolContext
from backend.tools.media_native import (
    extract_audio,
    extract_frame,
    probe_media,
    transcribe_local,
)

_ASSETS = Path(__file__).resolve().parents[1] / "assets"

requires_ffmpeg = pytest.mark.skipif(
    resolve_ffmpeg() is None,
    reason="ffmpeg não disponível neste ambiente (instale ou rode `scons ffmpeg`)",
)


@pytest.mark.asyncio
@requires_ffmpeg
class TestProbeMedia:
    async def test_video_real_devolve_duracao_e_streams(self, tmp_path):
        video = tmp_path / "sample.mp4"
        video.write_bytes((_ASSETS / "sample.mp4").read_bytes())

        with patch("backend.tools.fs._confine", return_value=(video, "")):
            result = json.loads(await probe_media(ctx=ToolContext(), path="sample.mp4"))

        assert float(result["format"]["duration"]) == pytest.approx(2.0, abs=0.2)
        codec_types = {s["codec_type"] for s in result["streams"]}
        assert codec_types == {"video", "audio"}

    async def test_arquivo_invalido_devolve_erro_sem_derrubar(self, tmp_path):
        """Erro/borda: arquivo que não é mídia nenhuma — ffprobe falha,
        vira erro tipado, nunca uma exceção crua."""
        fake = tmp_path / "nao-e-video.mp4"
        fake.write_text("isso não é um vídeo")

        with patch("backend.tools.fs._confine", return_value=(fake, "")):
            result = json.loads(
                await probe_media(ctx=ToolContext(), path="nao-e-video.mp4")
            )

        assert "error" in result

    @pytest.mark.parametrize(
        ("filename", "expected_codec_types"),
        [
            ("sample.wav", {"audio"}),
            ("sample.ogg", {"audio"}),
            ("sample.mp3", {"audio"}),
            ("sample.png", {"video"}),
            ("sample.jpg", {"video"}),
            ("sample.webp", {"video"}),
            ("sample.gif", {"video"}),
            ("sample.webm", {"video", "audio"}),
        ],
    )
    async def test_formatos_variados_sao_reconhecidos_sem_erro(
        self, tmp_path, filename, expected_codec_types
    ):
        """Diversidade de container/codec que o ffmpeg embutido precisa
        suportar na prática — não só o mp4/wav já cobertos acima."""
        asset = tmp_path / filename
        asset.write_bytes((_ASSETS / filename).read_bytes())

        with patch("backend.tools.fs._confine", return_value=(asset, "")):
            result = json.loads(await probe_media(ctx=ToolContext(), path=filename))

        assert "error" not in result
        codec_types = {s["codec_type"] for s in result["streams"]}
        assert codec_types == expected_codec_types


@pytest.mark.asyncio
@requires_ffmpeg
class TestExtractFrame:
    async def test_extrai_frame_real_do_video(self, tmp_path):
        video = tmp_path / "sample.mp4"
        video.write_bytes((_ASSETS / "sample.mp4").read_bytes())

        with patch("backend.tools.fs._confine", return_value=(video, "")):
            result = json.loads(
                await extract_frame(
                    ctx=ToolContext(), path="sample.mp4", timestamp_s=1.0
                )
            )

        assert "error" not in result
        frame_path = Path(result["path"])
        assert frame_path.is_file()
        assert (
            frame_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        )  # assinatura PNG real

    async def test_timestamp_negativo_e_erro_de_validacao_sem_chamar_ffmpeg(
        self, tmp_path
    ):
        """Erro/borda: timestamp inválido rejeitado ANTES de qualquer
        subprocess — não é o ffmpeg que precisa validar isso."""
        video = tmp_path / "sample.mp4"
        video.write_bytes((_ASSETS / "sample.mp4").read_bytes())

        with patch("backend.tools.fs._confine", return_value=(video, "")):
            result = json.loads(
                await extract_frame(
                    ctx=ToolContext(), path="sample.mp4", timestamp_s=-1.0
                )
            )

        assert "error" in result

    async def test_extrai_frame_do_gif_animado(self, tmp_path):
        """ffprobe lê GIF animado como stream de vídeo — prova que
        extract_frame funciona igual a um container de vídeo comum."""
        gif = tmp_path / "sample.gif"
        gif.write_bytes((_ASSETS / "sample.gif").read_bytes())

        with patch("backend.tools.fs._confine", return_value=(gif, "")):
            result = json.loads(
                await extract_frame(
                    ctx=ToolContext(), path="sample.gif", timestamp_s=0.5
                )
            )

        assert "error" not in result
        frame_path = Path(result["path"])
        assert frame_path.is_file()
        assert (
            frame_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
        )  # assinatura PNG real


@pytest.mark.asyncio
@requires_ffmpeg
class TestExtractAudio:
    async def test_extrai_audio_real_do_video(self, tmp_path):
        video = tmp_path / "sample.mp4"
        video.write_bytes((_ASSETS / "sample.mp4").read_bytes())

        with patch("backend.tools.fs._confine", return_value=(video, "")):
            result = json.loads(
                await extract_audio(ctx=ToolContext(), path="sample.mp4")
            )

        assert "error" not in result
        wav_path = Path(result["path"])
        assert wav_path.is_file()
        assert wav_path.read_bytes()[:4] == b"RIFF"  # assinatura WAV real


@pytest.mark.asyncio
class TestTranscribeLocal:
    @pytest.mark.live
    async def test_audio_real_via_faster_whisper(self, tmp_path):
        """Sem mock de faster-whisper — roda o modelo de verdade (baixado
        sob demanda da Hugging Face na primeira execução, cacheado em
        ~/.vectora/models/whisper/). Toca rede real — marcado `live`
        (mesmo padrão de `test_tools_youtube.py`), só roda via
        `scons tests-live`. O áudio é um tom senoidal puro (sem fala),
        então o teste verifica a ESTRUTURA da resposta, não o texto (que
        deve ficar vazio/quase vazio — não há fala nenhuma pra
        reconhecer)."""
        pytest.importorskip("faster_whisper")

        audio = tmp_path / "sample.wav"
        audio.write_bytes((_ASSETS / "sample.wav").read_bytes())

        with patch("backend.tools.fs._confine", return_value=(audio, "")):
            result = json.loads(
                await transcribe_local(ctx=ToolContext(), path="sample.wav")
            )

        assert "error" not in result
        assert "text" in result
        assert "language" in result

    async def test_falha_do_motor_de_transcricao_devolve_erro_tipado(
        self, tmp_path, monkeypatch
    ):
        """Falhas do motor são convertidas em erro legível pela tool."""
        import builtins

        audio = tmp_path / "sample.wav"
        audio.write_bytes((_ASSETS / "sample.wav").read_bytes())

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "faster_whisper":
                raise ImportError("no module named faster_whisper")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)

        with patch("backend.tools.fs._confine", return_value=(audio, "")):
            result = json.loads(
                await transcribe_local(ctx=ToolContext(), path="sample.wav")
            )

        assert "error" in result
        assert "falha ao transcrever" in result["error"]
