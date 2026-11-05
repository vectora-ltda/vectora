"""``backend/tools/youtube.py`` — transcrição de vídeo (YouTube e outras
plataformas via yt-dlp), transcript-first sem baixar mídia.

Guardado em duas camadas, mesmo padrão de ``test_llm_live.py``:
- Parsers/formatadores puros (``_extract_video_id``/``_format_transcript``)
  e ``get_transcript`` com as chamadas de rede mockadas — sempre rodam,
  fazem parte de ``scons tests``.
- Um teste ``live`` contra um vídeo público real e permanente (o primeiro
  vídeo do YouTube, "Me at the zoo") — só roda via ``scons tests-live``.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from backend.tools.context import ToolContext
from backend.tools.youtube import (
    _extract_video_id,
    _format_transcript,
    get_transcript,
    youtube_frame_at,
)

# ---------------------------------------------------------------------------
# _extract_video_id — parsing puro
# ---------------------------------------------------------------------------


class TestExtractVideoId:
    def test_reconhece_as_formas_comuns_de_url_e_rejeita_o_que_nao_e_video(self):
        casos = {
            "https://www.youtube.com/watch?v=jNQXAC9IVRw": "jNQXAC9IVRw",
            "https://youtu.be/jNQXAC9IVRw": "jNQXAC9IVRw",
            "https://www.youtube.com/shorts/jNQXAC9IVRw": "jNQXAC9IVRw",
            "https://www.youtube.com/embed/jNQXAC9IVRw": "jNQXAC9IVRw",
            "https://www.youtube.com/watch?v=jNQXAC9IVRw&t=10s": "jNQXAC9IVRw",
            "jNQXAC9IVRw": "jNQXAC9IVRw",  # ID cru
        }
        for url, expected in casos.items():
            assert _extract_video_id(url) == expected, url

        # Erro/borda no mesmo teste: não é vídeo nenhum — não pode inventar
        # um ID nem lançar, só devolver None.
        assert _extract_video_id("https://example.com/nao-e-youtube") is None
        assert _extract_video_id("") is None
        assert _extract_video_id("curto") is None


# ---------------------------------------------------------------------------
# _format_transcript — formatação pura
# ---------------------------------------------------------------------------


class TestFormatTranscript:
    def test_formata_com_timestamps_mmss_e_ignora_entradas_vazias(self):
        entries = [
            {"text": "primeiro", "start": 1.2, "duration": 2.0},
            {"text": "  ", "start": 5.0, "duration": 1.0},  # só espaço — some
            {"text": "depois de um minuto", "start": 61.0, "duration": 3.0},
        ]

        result = _format_transcript(entries)

        assert result == "[00:01] primeiro\n[01:01] depois de um minuto"

        # Erro/borda: lista vazia não pode quebrar, só devolver string vazia.
        assert _format_transcript([]) == ""


# ---------------------------------------------------------------------------
# get_transcript — orquestração (rede mockada)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestGetTranscript:
    @pytest.mark.parametrize(
        ("mime_type", "expected_suffix"),
        [("audio/mp4", "m4a"), ("audio/opus", "opus"), ("audio/webm", "webm")],
    )
    async def test_fallback_preserva_extensao_aceita_pelo_stt(
        self, mime_type: str, expected_suffix: str
    ) -> None:
        import backend.tools.youtube as mod

        assert mod._audio_suffix(mime_type) == expected_suffix

    async def test_url_invalida_devolve_erro_sem_tocar_em_rede(self):
        result = json.loads(await get_transcript(url="https://example.com/x"))
        assert "error" in result

    async def test_com_captions_publicas_usa_o_caminho_leve(self, monkeypatch):
        """Caminho feliz: legendas existem — nunca baixa áudio nenhum."""
        import backend.tools.youtube as mod

        monkeypatch.setattr(
            mod,
            "_fetch_captions_sync",
            lambda video_id, languages: [
                {"text": "ola mundo", "start": 0, "duration": 1}
            ],
        )
        chamou_audio = {"sim": False}

        def _fake_download(_url):
            chamou_audio["sim"] = True
            return b"", "audio/webm"

        monkeypatch.setattr(mod, "_download_audio_sync", _fake_download)

        result = json.loads(
            await get_transcript(url="https://www.youtube.com/watch?v=jNQXAC9IVRw")
        )

        assert result["source"] == "captions"
        assert result["video_id"] == "jNQXAC9IVRw"
        assert "[00:00] ola mundo" in result["transcript"]
        assert chamou_audio["sim"] is False

    async def test_sem_captions_cai_pro_fallback_de_audio_sem_baixar_video(
        self, monkeypatch
    ):
        """Erro/borda: vídeo sem legendas (exceção da lib) — cai pro
        fallback de áudio, chamando transcribe_audio (LLM), não algum
        download de vídeo."""
        import backend.tools.youtube as mod

        def _sem_captions(video_id, languages):
            raise RuntimeError("TranscriptsDisabled")

        monkeypatch.setattr(mod, "_fetch_captions_sync", _sem_captions)
        monkeypatch.setattr(
            mod, "_download_audio_sync", lambda _url: (b"audio-bytes", "audio/webm")
        )

        with patch(
            "backend.llm.transcription.transcribe_audio",
            AsyncMock(return_value="transcrito via LLM"),
        ) as mock_transcribe:
            result = json.loads(
                await get_transcript(url="https://www.youtube.com/watch?v=jNQXAC9IVRw")
            )

        assert result["source"] == "audio_fallback"
        assert result["transcript"] == "transcrito via LLM"
        mock_transcribe.assert_called_once()
        # Confirma que o mime_type/nome batem com o que o download real devolveu.
        args = mock_transcribe.call_args
        assert args[0][0] == b"audio-bytes"
        assert args[0][2] == "audio/webm"

    async def test_falha_nos_dois_caminhos_vira_erro_tipado(self, monkeypatch):
        """Erro/borda: sem legendas E sem conseguir baixar áudio (ex.: vídeo
        indisponível/geobloqueado) — erro legível, nunca traceback cru."""
        import backend.tools.youtube as mod

        monkeypatch.setattr(
            mod,
            "_fetch_captions_sync",
            lambda *_a: (_ for _ in ()).throw(RuntimeError("sem legendas")),
        )
        monkeypatch.setattr(
            mod,
            "_download_audio_sync",
            lambda _url: (_ for _ in ()).throw(RuntimeError("vídeo indisponível")),
        )

        result = json.loads(
            await get_transcript(url="https://www.youtube.com/watch?v=jNQXAC9IVRw")
        )

        assert "error" in result


# ---------------------------------------------------------------------------
# youtube_frame_at — orquestração (rede/ffmpeg mockados)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestYoutubeFrameAt:
    async def test_timestamp_negativo_e_erro_sem_tocar_em_rede(self):
        result = json.loads(
            await youtube_frame_at(
                ctx=ToolContext(thread_id="t1"),
                url="https://www.youtube.com/watch?v=jNQXAC9IVRw",
                timestamp_s=-1.0,
            )
        )
        assert "error" in result

    async def test_url_invalida_e_erro_sem_tocar_em_rede(self):
        result = json.loads(
            await youtube_frame_at(
                ctx=ToolContext(thread_id="t1"),
                url="https://example.com/nao-e-youtube",
                timestamp_s=1.0,
            )
        )
        assert "error" in result

    async def test_baixa_so_um_trecho_curto_e_extrai_o_frame(
        self, monkeypatch, tmp_path
    ):
        """Prova que o fluxo é: baixar CLIPE curto (nunca o vídeo inteiro)
        → extrair frame do clipe → persistir → devolver url servível."""
        import backend.tools.youtube as mod

        clip_path = tmp_path / "clip.mp4"
        clip_path.write_bytes(b"fake-clip")
        monkeypatch.setattr(mod, "_download_clip_sync", lambda *_a: str(clip_path))

        async def _fake_extract(_ffmpeg, video_path, _ts, out_path):
            assert video_path == str(
                clip_path
            )  # extrai do CLIPE, não do vídeo original
            Path(out_path).write_bytes(b"\x89PNG-fake-frame")
            return True, ""

        monkeypatch.setattr(
            "backend.tools.media_native.extract_frame_to", _fake_extract
        )
        monkeypatch.setattr(
            "backend.services.ffmpeg_binary.resolve_ffmpeg", lambda: "/usr/bin/ffmpeg"
        )

        persisted = {}

        def _fake_persist(session_id, data, suffix):
            path = tmp_path / f"{session_id}{suffix}"
            path.write_bytes(data)
            persisted["session_id"] = session_id
            persisted["data"] = data
            return path

        monkeypatch.setattr("backend.tools.media._persist", _fake_persist)

        result = json.loads(
            await youtube_frame_at(
                ctx=ToolContext(thread_id="t-frame"),
                url="https://www.youtube.com/watch?v=jNQXAC9IVRw",
                timestamp_s=5.0,
            )
        )

        assert "error" not in result
        assert result["url"].startswith("/artifacts/t-frame/media/")
        assert persisted["data"] == b"\x89PNG-fake-frame"
        # O clipe temporário some depois de usado — não pode acumular lixo.
        assert not clip_path.exists()

    async def test_aceita_url_de_outra_plataforma_suportada_pelo_ytdlp(
        self, monkeypatch, tmp_path
    ):
        """`video_id` (regex do YouTube) nunca deveria ser um requisito pra
        extrair frame — `_download_clip_sync` já recebe a `url` original e
        funciona com qualquer extrator do yt-dlp. Prova com uma URL do
        Vimeo (nunca bate com `_extract_video_id`) que o fluxo completa,
        em vez de rejeitar cedo achando que só YouTube é suportado."""
        import backend.tools.youtube as mod

        clip_path = tmp_path / "clip.mp4"
        clip_path.write_bytes(b"fake-clip")
        monkeypatch.setattr(mod, "_download_clip_sync", lambda *_a: str(clip_path))

        async def _fake_extract(_ffmpeg, video_path, _ts, out_path):
            assert video_path == str(clip_path)
            Path(out_path).write_bytes(b"\x89PNG-fake-frame")
            return True, ""

        monkeypatch.setattr(
            "backend.tools.media_native.extract_frame_to", _fake_extract
        )
        monkeypatch.setattr(
            "backend.services.ffmpeg_binary.resolve_ffmpeg", lambda: "/usr/bin/ffmpeg"
        )
        monkeypatch.setattr(
            "backend.tools.media._persist",
            lambda session_id, data, suffix: tmp_path / f"{session_id}{suffix}",
        )

        result = json.loads(
            await youtube_frame_at(
                ctx=ToolContext(thread_id="t-vimeo"),
                url="https://vimeo.com/56015672",
                timestamp_s=2.0,
            )
        )

        assert "error" not in result

    async def test_falha_no_download_do_clipe_vira_erro_tipado(self, monkeypatch):
        """Erro/borda: vídeo indisponível/geobloqueado — erro legível, nunca
        traceback cru."""
        import backend.tools.youtube as mod

        def _falha(*_a):
            raise RuntimeError("vídeo indisponível")

        monkeypatch.setattr(mod, "_download_clip_sync", _falha)
        monkeypatch.setattr(
            "backend.services.ffmpeg_binary.resolve_ffmpeg", lambda: "/usr/bin/ffmpeg"
        )

        result = json.loads(
            await youtube_frame_at(
                ctx=ToolContext(thread_id="t1"),
                url="https://www.youtube.com/watch?v=jNQXAC9IVRw",
                timestamp_s=1.0,
            )
        )

        assert "error" in result


# ---------------------------------------------------------------------------
# Live — vídeo público real, sem mock (rede de verdade)
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.asyncio
async def test_get_transcript_video_publico_real_via_captions():
    """ "Me at the zoo" (jNQXAC9IVRw) — primeiro vídeo do YouTube, público,
    permanente, com legendas em inglês. Prova o caminho leve fim a fim
    contra a API real, sem baixar áudio/vídeo nenhum."""
    result = json.loads(
        await get_transcript(
            url="https://www.youtube.com/watch?v=jNQXAC9IVRw", language="en"
        )
    )

    assert result["source"] == "captions"
    assert result["video_id"] == "jNQXAC9IVRw"
    assert "elephant" in result["transcript"].lower()
    assert result["transcript"].startswith("[00:")


@pytest.mark.live
@pytest.mark.asyncio
async def test_youtube_frame_at_video_publico_real_extrai_frame_de_verdade():
    """Baixa um clipe curto real de "Me at the zoo" e extrai um frame PNG
    real — prova o fluxo completo (yt-dlp + ffmpeg) contra a rede/binário
    de verdade, sem nenhum mock."""
    result = json.loads(
        await youtube_frame_at(
            ctx=ToolContext(thread_id="t-live-frame"),
            url="https://www.youtube.com/watch?v=jNQXAC9IVRw",
            timestamp_s=3.0,
        )
    )

    assert "error" not in result
    assert result["video_id"] == "jNQXAC9IVRw"
    frame_path = Path(result["path"])
    assert frame_path.is_file()
    assert frame_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    frame_path.unlink()
    frame_path.parent.rmdir()


@pytest.mark.live
@pytest.mark.asyncio
async def test_youtube_frame_at_video_youtube_ultracurto_menos_de_15s():
    """ "Shortest Video on Youtube" (tPEE9ZwTmy0) — 1 segundo, público,
    permanente. Prova o requisito de vídeo <15s no próprio YouTube, além
    do "Me at the zoo" (19s) usado acima."""
    result = json.loads(
        await youtube_frame_at(
            ctx=ToolContext(thread_id="t-live-short"),
            url="https://www.youtube.com/watch?v=tPEE9ZwTmy0",
            timestamp_s=0.3,
        )
    )

    assert "error" not in result
    assert result["video_id"] == "tPEE9ZwTmy0"
    frame_path = Path(result["path"])
    assert frame_path.is_file()
    assert frame_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    frame_path.unlink()
    frame_path.parent.rmdir()


@pytest.mark.live
@pytest.mark.asyncio
async def test_youtube_frame_at_plataforma_nao_youtube_archive_org():
    """Internet Archive ("test.m4v", ~10.78s) — hospedagem sem fins
    lucrativos, sem login, permanente. Vimeo foi descartado como
    plataforma de teste: seus fixtures antigos (mantidos no próprio
    test suite do yt-dlp) hoje exigem login ("The web client only works
    when logged-in"), confirmado ao vivo nesta sessão — mudança de
    política da Vimeo, não bug daqui. Prova de fato — não só de prosa —
    que o pipeline de frame extraction funciona fora do YouTube, sem
    chamar LLM nenhuma (só yt-dlp + ffmpeg)."""
    result = json.loads(
        await youtube_frame_at(
            ctx=ToolContext(thread_id="t-live-archive-org"),
            url="https://archive.org/details/testvideo_20230410_202304",
            timestamp_s=3.0,
        )
    )

    assert "error" not in result
    frame_path = Path(result["path"])
    assert frame_path.is_file()
    assert frame_path.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    frame_path.unlink()
    frame_path.parent.rmdir()
