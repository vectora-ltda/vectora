"""Imagem e TTS nativos do OpenRouter.

Os dois formatos de retorno são **opostos**: `POST /images` devolve base64
dentro de JSON (`data[].b64_json`) e `POST /audio/speech` devolve
bytestream binário. Tratar os dois pelo mesmo caminho quebra um deles —
imagem viraria arquivo de base64 em texto, ou áudio viraria lixo
decodificado.
"""

from __future__ import annotations

import base64

import httpx
import pytest

from backend.llm.openrouter.client import OpenRouterClient, OpenRouterResponseError
from backend.llm.openrouter.media import generate_image_bytes, synthesize_speech_bytes

#: PNG mínimo válido — o conteúdo importa: o teste de formato compara bytes.
_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_MP3 = b"ID3\x04\x00\x00\x00\x00\x00\x00audio-cru"


def _client(handler) -> OpenRouterClient:
    return OpenRouterClient(
        api_key="sk-or-test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


class TestImagem:
    @pytest.mark.asyncio
    async def test_b64_json_e_decodificado_antes_de_gravar(self):
        def handler(_req):
            return httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(_PNG).decode("ascii")}]},
            )

        dados = await generate_image_bytes(
            _client(handler), model="openai/gpt-image-1", prompt="um gato"
        )

        assert dados == _PNG
        assert dados.startswith(b"\x89PNG"), "gravou o base64 em texto, não a imagem"

    @pytest.mark.asyncio
    async def test_parametros_opcionais_vao_no_payload(self):
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            import json as _json

            capturado.update(_json.loads(req.content))
            return httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(_PNG).decode("ascii")}]},
            )

        await generate_image_bytes(
            _client(handler),
            model="m",
            prompt="p",
            size="1024x1024",
            output_format="png",
        )

        assert capturado["size"] == "1024x1024"
        assert capturado["output_format"] == "png"
        # Erro/borda: `n` não pedido não pode ir como `null` — alguns
        # providers roteados rejeitam o campo nulo com 400.
        assert "n" not in capturado

    @pytest.mark.asyncio
    async def test_b64_json_ausente_vira_erro_sem_gravar_arquivo_vazio(self):
        """Erro/borda: filtro do provider devolve `data` sem imagem. Sem este
        corte, grava um arquivo de 0 byte que aparece como artifact quebrado."""

        def handler(_req):
            return httpx.Response(200, json={"data": [{"revised_prompt": "..."}]})

        with pytest.raises(OpenRouterResponseError):
            await generate_image_bytes(_client(handler), model="m", prompt="p")

    @pytest.mark.asyncio
    async def test_data_vazio_tambem_falha(self):
        def handler(_req):
            return httpx.Response(200, json={"data": []})

        with pytest.raises(OpenRouterResponseError):
            await generate_image_bytes(_client(handler), model="m", prompt="p")

    @pytest.mark.asyncio
    async def test_base64_corrompido_vira_erro_tipado(self):
        """Erro/borda: `b64decode` de lixo estoura `binascii.Error` cru."""

        def handler(_req):
            return httpx.Response(200, json={"data": [{"b64_json": "!!!nao-e-base64"}]})

        with pytest.raises(OpenRouterResponseError):
            await generate_image_bytes(_client(handler), model="m", prompt="p")


class TestTTS:
    @pytest.mark.asyncio
    async def test_bytestream_usado_direto_sem_b64decode(self):
        def handler(_req):
            return httpx.Response(
                200, content=_MP3, headers={"content-type": "audio/mpeg"}
            )

        dados = await synthesize_speech_bytes(
            _client(handler), model="openai/gpt-4o-mini-tts", text="olá", voice="alloy"
        )

        assert dados == _MP3, "o áudio passou por b64decode e virou lixo"

    @pytest.mark.asyncio
    async def test_response_format_e_speed_vao_no_payload(self):
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            import json as _json

            capturado.update(_json.loads(req.content))
            return httpx.Response(200, content=_MP3)

        await synthesize_speech_bytes(
            _client(handler),
            model="m",
            text="oi",
            voice="alloy",
            response_format="mp3",
            speed=1.25,
        )

        assert capturado["response_format"] == "mp3"
        assert capturado["speed"] == 1.25
        assert capturado["voice"] == "alloy"

    @pytest.mark.asyncio
    async def test_corpo_vazio_vira_erro_sem_gravar_audio_de_zero_byte(self):
        def handler(_req):
            return httpx.Response(200, content=b"")

        with pytest.raises(OpenRouterResponseError):
            await synthesize_speech_bytes(
                _client(handler), model="m", text="oi", voice="alloy"
            )

    @pytest.mark.asyncio
    async def test_429_vira_erro_tipado_e_nao_arquivo_com_json_de_erro(self):
        """Erro/borda: sem checar status, o corpo de erro (JSON) seria gravado
        como se fosse áudio — arquivo que abre e não toca."""
        from backend.llm.openrouter.client import OpenRouterRateLimitError

        def handler(_req):
            return httpx.Response(429, json={"error": {"message": "slow down"}})

        with pytest.raises(OpenRouterRateLimitError):
            await synthesize_speech_bytes(
                _client(handler), model="m", text="oi", voice="alloy"
            )


class TestFormatosSaoOpostos:
    """Imagem decodifica o base64 recebido; áudio usa o bytestream direto.

    Se alguém unificar os dois caminhos, um destes assert cai.
    """

    @pytest.mark.asyncio
    async def test_imagem_decodifica_e_audio_nao(self):
        conteudo_audio = base64.b64encode(b"nao-decodificar").decode("ascii").encode()

        def handler_img(_req):
            return httpx.Response(
                200,
                json={"data": [{"b64_json": base64.b64encode(_PNG).decode("ascii")}]},
            )

        def handler_tts(_req):
            return httpx.Response(200, content=conteudo_audio)

        img = await generate_image_bytes(_client(handler_img), model="m", prompt="p")
        audio = await synthesize_speech_bytes(
            _client(handler_tts), model="m", text="t", voice="v"
        )

        # Imagem: veio base64 no JSON e saiu decodificada.
        assert img == _PNG
        # Áudio: veio binário e saiu idêntico — nada de b64decode no caminho.
        assert audio == conteudo_audio
