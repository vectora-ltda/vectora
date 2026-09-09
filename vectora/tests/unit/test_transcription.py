"""transcribe_audio — STT via OpenAI Whisper, com fallback pra Gemini."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from backend.llm.transcription import TranscriptionError, transcribe_audio


class TestTranscribeAudio:
    @pytest.mark.asyncio
    async def test_returns_stripped_text_on_success(self) -> None:
        response = MagicMock()
        response.json.return_value = {"text": "  olá mundo  "}
        response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("backend.llm.transcription.settings.openai_api_key", "sk-test"),
            patch(
                "backend.llm.transcription.httpx.AsyncClient", return_value=mock_client
            ),
        ):
            text = await transcribe_audio(
                b"audio-bytes",
                "memo.mp3",
                "audio/mpeg",
                provider="openai",
                model="whisper-1",
                language="",
            )

        assert text == "olá mundo"
        mock_client.post.assert_awaited_once()
        _, kwargs = mock_client.post.call_args
        assert kwargs["files"]["file"][0] == "memo.mp3"

    @pytest.mark.asyncio
    async def test_raises_when_no_key_configured(self) -> None:
        with (
            patch("backend.llm.transcription.settings.openai_api_key", None),
            patch("backend.llm.transcription.settings.google_api_key", None),
        ):
            with pytest.raises(TranscriptionError, match="openai_api_key"):
                await transcribe_audio(
                    b"audio-bytes",
                    "memo.mp3",
                    "audio/mpeg",
                    provider="openai",
                    model="whisper-1",
                    language="",
                )

    @pytest.mark.asyncio
    async def test_raises_transcription_error_on_http_failure(self) -> None:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("falha de rede"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("backend.llm.transcription.settings.openai_api_key", "sk-test"),
            patch(
                "backend.llm.transcription.httpx.AsyncClient", return_value=mock_client
            ),
        ):
            with pytest.raises(TranscriptionError):
                await transcribe_audio(
                    b"audio-bytes",
                    "memo.mp3",
                    "audio/mpeg",
                    provider="openai",
                    model="whisper-1",
                    language="",
                )


class TestTranscribeAudioGeminiFallback:
    @pytest.mark.asyncio
    async def test_falls_back_to_gemini_when_openai_key_missing(self) -> None:
        fake_response = MagicMock()
        fake_response.text = "  transcrição via gemini  "

        fake_models = AsyncMock()
        fake_models.generate_content = AsyncMock(return_value=fake_response)
        fake_client = MagicMock()
        fake_client.aio.models = fake_models

        with (
            patch("backend.llm.transcription.settings.openai_api_key", None),
            patch(
                "backend.llm.transcription.settings.google_api_key",
                "google-test-key",
            ),
            patch("google.genai.Client", return_value=fake_client) as mock_cls,
        ):
            text = await transcribe_audio(
                b"audio-bytes",
                "ditado.webm",
                "audio/webm",
                provider="google-genai",
                model="whisper-1",
                language="",
            )

        assert text == "transcrição via gemini"
        mock_cls.assert_called_once_with(api_key="google-test-key")
        fake_models.generate_content.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_gemini_failure_raises_transcription_error(self) -> None:
        fake_models = AsyncMock()
        fake_models.generate_content = AsyncMock(side_effect=RuntimeError("api down"))
        fake_client = MagicMock()
        fake_client.aio.models = fake_models

        with (
            patch("backend.llm.transcription.settings.openai_api_key", None),
            patch(
                "backend.llm.transcription.settings.google_api_key",
                "google-test-key",
            ),
            patch("google.genai.Client", return_value=fake_client),
        ):
            with pytest.raises(TranscriptionError):
                await transcribe_audio(
                    b"audio-bytes",
                    "ditado.webm",
                    "audio/webm",
                    provider="google-genai",
                    model="whisper-1",
                    language="",
                )

    @pytest.mark.asyncio
    async def test_gemini_503_retenta_e_recupera(self) -> None:
        """503 UNAVAILABLE (alta demanda) é transitório — retenta antes de falhar."""
        from google.genai.errors import ServerError

        overloaded = ServerError(503, {"error": {"message": "high demand"}}, None)
        fake_response = MagicMock()
        fake_response.text = "recuperou na segunda tentativa"

        fake_models = AsyncMock()
        fake_models.generate_content = AsyncMock(
            side_effect=[overloaded, fake_response]
        )
        fake_client = MagicMock()
        fake_client.aio.models = fake_models

        with (
            patch("backend.llm.transcription.settings.openai_api_key", None),
            patch(
                "backend.llm.transcription.settings.google_api_key",
                "google-test-key",
            ),
            patch("google.genai.Client", return_value=fake_client),
            patch("backend.llm.transcription.asyncio.sleep", AsyncMock()),
        ):
            text = await transcribe_audio(
                b"audio-bytes",
                "ditado.webm",
                "audio/webm",
                provider="google-genai",
                model="whisper-1",
                language="",
            )

        assert text == "recuperou na segunda tentativa"
        assert fake_models.generate_content.await_count == 2

    @pytest.mark.asyncio
    async def test_gemini_503_persistente_esgota_tentativas(self) -> None:
        from google.genai.errors import ServerError

        overloaded = ServerError(503, {"error": {"message": "high demand"}}, None)
        fake_models = AsyncMock()
        fake_models.generate_content = AsyncMock(side_effect=overloaded)
        fake_client = MagicMock()
        fake_client.aio.models = fake_models

        with (
            patch("backend.llm.transcription.settings.openai_api_key", None),
            patch(
                "backend.llm.transcription.settings.google_api_key",
                "google-test-key",
            ),
            patch("google.genai.Client", return_value=fake_client),
            patch("backend.llm.transcription.asyncio.sleep", AsyncMock()) as mock_sleep,
        ):
            with pytest.raises(TranscriptionError):
                await transcribe_audio(
                    b"audio-bytes",
                    "ditado.webm",
                    "audio/webm",
                    provider="google-genai",
                    model="whisper-1",
                    language="",
                )

        assert fake_models.generate_content.await_count == 3
        assert mock_sleep.await_count == 2

    @pytest.mark.asyncio
    async def test_openai_key_presente_ignora_gemini(self) -> None:
        """Com as duas chaves configuradas, OpenAI continua sendo a primária."""
        response = MagicMock()
        response.json.return_value = {"text": "via whisper"}
        response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.post = AsyncMock(return_value=response)
        mock_http_client.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_http_client.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("backend.llm.transcription.settings.openai_api_key", "sk-test"),
            patch(
                "backend.llm.transcription.settings.google_api_key",
                "google-test-key",
            ),
            patch(
                "backend.llm.transcription.httpx.AsyncClient",
                return_value=mock_http_client,
            ),
            patch("google.genai.Client") as mock_gemini_cls,
        ):
            text = await transcribe_audio(
                b"audio-bytes",
                "memo.mp3",
                "audio/mpeg",
                provider="openai",
                model="whisper-1",
                language="",
            )

        assert text == "via whisper"
        mock_gemini_cls.assert_not_called()
