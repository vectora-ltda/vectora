"""Vídeo do OpenRouter — ``POST /videos``, assíncrono de verdade.

Único endpoint do conjunto que **não** devolve o resultado na resposta:
HTTP 202 com ``id``/``polling_url``/``status``, ciclo
``pending → in_progress → completed|failed|cancelled|expired``.

A lição do incidente do NATS vale aqui: um loop de espera sem teto gira para
sempre quando o outro lado nunca conclui.
"""

from __future__ import annotations

import httpx
import pytest

from backend.llm.openrouter.client import OpenRouterClient, OpenRouterResponseError
from backend.llm.openrouter.video import (
    VideoTimeoutError,
    generate_video,
    start_video_generation,
)


def _client(handler) -> OpenRouterClient:
    return OpenRouterClient(
        api_key="sk-or-test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _resposta_status(status: str, **extra) -> dict:
    return {"id": "vid-1", "status": status, **extra}


class TestInicio:
    @pytest.mark.asyncio
    async def test_202_devolve_id_e_polling_url(self):
        def handler(_req):
            return httpx.Response(
                202,
                json={
                    "id": "vid-1",
                    "status": "pending",
                    "polling_url": "https://openrouter.ai/api/v1/videos/vid-1",
                },
            )

        job = await start_video_generation(
            _client(handler), model="google/veo-3", prompt="um gato correndo"
        )

        assert job.id == "vid-1"
        assert job.status == "pending"
        assert job.polling_url.endswith("/videos/vid-1")

    @pytest.mark.asyncio
    async def test_resposta_sem_id_vira_erro_tipado(self):
        """Erro/borda: sem `id` não há como consultar o progresso — o job
        estaria perdido, rodando e cobrando sem ninguém buscar o resultado."""

        def handler(_req):
            return httpx.Response(202, json={"status": "pending"})

        with pytest.raises(OpenRouterResponseError, match="id"):
            await start_video_generation(_client(handler), model="m", prompt="p")

    @pytest.mark.asyncio
    async def test_callback_url_vai_no_payload_quando_informado(self):
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            import json as _json

            capturado.update(_json.loads(req.content))
            return httpx.Response(202, json={"id": "v", "status": "pending"})

        await start_video_generation(
            _client(handler),
            model="m",
            prompt="p",
            callback_url="https://exemplo.test/hook",
        )

        assert capturado["callback_url"] == "https://exemplo.test/hook"


class TestPolling:
    @pytest.mark.asyncio
    async def test_pending_ate_completed_devolve_a_url_do_video(self):
        estados = iter(["pending", "in_progress", "in_progress"])

        def handler(req: httpx.Request) -> httpx.Response:
            if req.method == "POST":
                return httpx.Response(202, json={"id": "vid-1", "status": "pending"})
            try:
                return httpx.Response(200, json=_resposta_status(next(estados)))
            except StopIteration:
                return httpx.Response(
                    200,
                    json=_resposta_status(
                        "completed", output={"url": "https://cdn.test/v.mp4"}
                    ),
                )

        resultado = await generate_video(
            _client(handler),
            model="m",
            prompt="p",
            poll_interval_s=0,
            timeout_s=30,
        )

        assert resultado["output"]["url"] == "https://cdn.test/v.mp4"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("estado", ["failed", "cancelled", "expired"])
    async def test_estado_terminal_de_falha_vira_erro_tipado(self, estado):
        """Erro/borda: os três estados terminais que não são `completed`
        precisam sair do loop com erro, não continuar consultando."""

        def handler(req: httpx.Request) -> httpx.Response:
            if req.method == "POST":
                return httpx.Response(202, json={"id": "vid-1", "status": "pending"})
            return httpx.Response(200, json=_resposta_status(estado, error="deu ruim"))

        with pytest.raises(OpenRouterResponseError, match=estado):
            await generate_video(
                _client(handler), model="m", prompt="p", poll_interval_s=0, timeout_s=30
            )

    @pytest.mark.asyncio
    async def test_polling_que_nunca_conclui_respeita_o_teto(self):
        """Erro/borda crítico: mesma lição do incidente do NATS — loop sem
        teto gira para sempre quando o outro lado nunca conclui."""
        consultas = 0

        def handler(req: httpx.Request) -> httpx.Response:
            nonlocal consultas
            if req.method == "POST":
                return httpx.Response(202, json={"id": "vid-1", "status": "pending"})
            consultas += 1
            return httpx.Response(200, json=_resposta_status("in_progress"))

        with pytest.raises(VideoTimeoutError):
            await generate_video(
                _client(handler),
                model="m",
                prompt="p",
                poll_interval_s=0,
                timeout_s=0.05,
            )

        assert consultas > 0, "desistiu antes de consultar uma vez sequer"

    @pytest.mark.asyncio
    async def test_status_desconhecido_nao_trava_nem_estoura(self):
        """Erro/borda: status fora do ciclo documentado (API evoluiu) segue
        sendo tratado como "ainda rodando" até o teto — nunca KeyError."""

        def handler(req: httpx.Request) -> httpx.Response:
            if req.method == "POST":
                return httpx.Response(202, json={"id": "vid-1", "status": "pending"})
            return httpx.Response(200, json=_resposta_status("aquecendo_motores"))

        with pytest.raises(VideoTimeoutError):
            await generate_video(
                _client(handler),
                model="m",
                prompt="p",
                poll_interval_s=0,
                timeout_s=0.05,
            )

    @pytest.mark.asyncio
    async def test_completed_sem_output_vira_erro(self):
        """Erro/borda: concluir sem entregar o vídeo é falha, não sucesso."""

        def handler(req: httpx.Request) -> httpx.Response:
            if req.method == "POST":
                return httpx.Response(202, json={"id": "vid-1", "status": "pending"})
            return httpx.Response(200, json=_resposta_status("completed"))

        with pytest.raises(OpenRouterResponseError, match="output"):
            await generate_video(
                _client(handler), model="m", prompt="p", poll_interval_s=0, timeout_s=30
            )

    @pytest.mark.asyncio
    async def test_completed_ja_na_primeira_consulta_nao_espera_a_toa(self):
        def handler(req: httpx.Request) -> httpx.Response:
            if req.method == "POST":
                return httpx.Response(202, json={"id": "vid-1", "status": "pending"})
            return httpx.Response(
                200,
                json=_resposta_status(
                    "completed", output={"url": "https://cdn.test/v.mp4"}
                ),
            )

        resultado = await generate_video(
            _client(handler), model="m", prompt="p", poll_interval_s=99, timeout_s=30
        )

        assert resultado["status"] == "completed"
