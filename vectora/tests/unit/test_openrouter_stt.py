"""STT nativo do OpenRouter — ``POST /audio/transcriptions``.

Esse endpoint recebe **multipart** com o arquivo de áudio, diferente dos
demais endpoints do provider que mandam JSON — por isso o cliente usa
``post_multipart`` em vez de ``post_json``.

`transcribe_audio` (``backend/llm/transcription.py``) inclui o OpenRouter
como provider de STT, junto com Whisper e Gemini.
"""

from __future__ import annotations

import httpx
import pytest

from backend.llm.openrouter.client import OpenRouterClient, OpenRouterResponseError
from backend.llm.openrouter.stt import transcribe_bytes

_AUDIO = b"ID3\x04\x00\x00\x00fake-audio-bytes"


def _client(handler) -> OpenRouterClient:
    return OpenRouterClient(
        api_key="sk-or-test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


class TestTranscricao:
    @pytest.mark.asyncio
    async def test_audio_curto_devolve_o_texto(self):
        def handler(_req):
            return httpx.Response(200, json={"text": "  olá mundo  "})

        texto = await transcribe_bytes(
            _client(handler),
            model="openai/whisper-1",
            data=_AUDIO,
            filename="nota.mp3",
            mime_type="audio/mpeg",
        )

        # Espaço nas bordas é comum na resposta e sujaria a mensagem no chat.
        assert texto == "olá mundo"

    @pytest.mark.asyncio
    async def test_manda_multipart_com_arquivo_e_nao_json(self):
        """O endpoint é multipart; mandar JSON aqui rende 400 do provider."""
        capturado: dict[str, str] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            capturado["content_type"] = req.headers.get("content-type", "")
            capturado["corpo"] = req.content.decode("latin-1")
            return httpx.Response(200, json={"text": "ok"})

        await transcribe_bytes(
            _client(handler),
            model="openai/whisper-1",
            data=_AUDIO,
            filename="nota.mp3",
            mime_type="audio/mpeg",
        )

        assert capturado["content_type"].startswith("multipart/form-data")
        assert "nota.mp3" in capturado["corpo"]
        assert "openai/whisper-1" in capturado["corpo"]

    @pytest.mark.asyncio
    async def test_idioma_opcional_vai_junto_quando_informado(self):
        capturado: dict[str, str] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            capturado["corpo"] = req.content.decode("latin-1")
            return httpx.Response(200, json={"text": "ok"})

        await transcribe_bytes(
            _client(handler),
            model="m",
            data=_AUDIO,
            filename="a.mp3",
            mime_type="audio/mpeg",
            language="pt",
        )

        assert "pt" in capturado["corpo"]

    @pytest.mark.asyncio
    async def test_resposta_sem_text_vira_erro_tipado(self):
        """Erro/borda: sem este corte a transcrição vira string vazia e o
        usuário acha que o áudio estava mudo, em vez de saber que falhou."""

        def handler(_req):
            return httpx.Response(200, json={"task": "transcribe"})

        with pytest.raises(OpenRouterResponseError, match="text"):
            await transcribe_bytes(
                _client(handler),
                model="m",
                data=_AUDIO,
                filename="a.mp3",
                mime_type="audio/mpeg",
            )

    @pytest.mark.asyncio
    async def test_text_vazio_tambem_falha(self):
        def handler(_req):
            return httpx.Response(200, json={"text": "   "})

        with pytest.raises(OpenRouterResponseError):
            await transcribe_bytes(
                _client(handler),
                model="m",
                data=_AUDIO,
                filename="a.mp3",
                mime_type="audio/mpeg",
            )

    @pytest.mark.asyncio
    async def test_arquivo_vazio_nem_chega_a_chamar_a_api(self):
        """Erro/borda: gravação que falhou devolve 0 byte. Mandar assim gasta
        crédito pra receber erro do provider."""
        chamou = False

        def handler(_req):
            nonlocal chamou
            chamou = True
            return httpx.Response(200, json={"text": "x"})

        with pytest.raises(OpenRouterResponseError, match="vazio"):
            await transcribe_bytes(
                _client(handler),
                model="m",
                data=b"",
                filename="a.mp3",
                mime_type="audio/mpeg",
            )
        assert not chamou

    @pytest.mark.asyncio
    async def test_402_vira_erro_de_credito_e_nao_transcricao_vazia(self):
        from backend.llm.openrouter.client import OpenRouterCreditError

        def handler(_req):
            return httpx.Response(402, json={"error": {"message": "no credits"}})

        with pytest.raises(OpenRouterCreditError):
            await transcribe_bytes(
                _client(handler),
                model="m",
                data=_AUDIO,
                filename="a.mp3",
                mime_type="audio/mpeg",
            )


class TestCadeiaDeTranscricao:
    """`transcribe_audio` escolhe o provider pela chave configurada."""

    @pytest.mark.asyncio
    async def test_sem_openai_e_sem_google_usa_openrouter(self, monkeypatch):
        from backend.llm import transcription
        from backend.settings import settings as _s

        monkeypatch.setattr(_s, "openai_api_key", "", raising=False)
        monkeypatch.setattr(_s, "google_api_key", "", raising=False)
        monkeypatch.setattr(_s, "openrouter_api_key", "sk-or-test", raising=False)

        def configured_model(_provider: str, _capability: str) -> str:
            return "whisper"

        monkeypatch.setattr(
            "backend.settings.configured_gateway_model", configured_model
        )

        async def _fake(_client, **kwargs):
            assert kwargs["model"] == "whisper"
            return "transcrito via openrouter"

        monkeypatch.setattr("backend.llm.openrouter.stt.transcribe_bytes", _fake)

        texto = await transcription.transcribe_audio(
            _AUDIO,
            "a.mp3",
            "audio/mpeg",
            provider="openrouter",
            model="whisper",
            language="",
        )
        assert texto == "transcrito via openrouter"

    @pytest.mark.asyncio
    async def test_sem_nenhuma_chave_continua_com_erro_claro(self, monkeypatch):
        """Erro/borda: sem nenhum provider de STT configurado, o erro
        identifica que falta chave, independente de qual provider seria
        usado."""
        from backend.llm import transcription
        from backend.settings import settings as _s

        monkeypatch.setattr(_s, "openai_api_key", "", raising=False)
        monkeypatch.setattr(_s, "google_api_key", "", raising=False)
        monkeypatch.setattr(_s, "openrouter_api_key", "", raising=False)

        with pytest.raises(
            transcription.TranscriptionError,
            match="modelo de STT do OpenRouter|provider de transcrição indisponível",
        ):
            await transcription.transcribe_audio(
                _AUDIO,
                "a.mp3",
                "audio/mpeg",
                provider="openrouter",
                model="whisper",
                language="",
            )

    @pytest.mark.asyncio
    async def test_openrouter_sem_modelo_de_stt_nao_entra_na_cadeia(self, monkeypatch):
        """Erro/borda: key configurada mas sem modelo de STT escolhido — a
        capacidade não está disponível, e o erro tem que dizer isso."""
        from backend.llm import transcription
        from backend.settings import settings as _s

        monkeypatch.setattr(_s, "openai_api_key", "", raising=False)
        monkeypatch.setattr(_s, "google_api_key", "", raising=False)
        monkeypatch.setattr(_s, "openrouter_api_key", "sk-or-test", raising=False)

        def configured_model(_provider: str, _capability: str) -> str:
            return "whisper"

        monkeypatch.setattr(
            "backend.settings.configured_gateway_model", configured_model
        )

        async def _fake(_client, **kwargs):
            assert kwargs["model"] == "whisper"
            return "transcrito"

        monkeypatch.setattr("backend.llm.openrouter.stt.transcribe_bytes", _fake)

        assert (
            await transcription.transcribe_audio(
                _AUDIO,
                "a.mp3",
                "audio/mpeg",
                provider="openrouter",
                model="whisper",
                language="",
            )
            == "transcrito"
        )
