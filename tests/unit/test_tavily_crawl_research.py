"""Crawl, map e research do Tavily — endpoints além da busca simples.

Três naturezas diferentes:

- ``crawl``/``map`` são **caros e pesados**: varrem um site inteiro, gastam
  créditos por página e batem no alvo. Entram com HITL.
- ``research`` é **assíncrono**: `POST /research` responde 201 com
  `request_id`/`status`, e o resultado vem por polling. Mesmo padrão do vídeo
  do OpenRouter, e a mesma trava: teto de tempo obrigatório.
"""

from __future__ import annotations

import httpx
import pytest

from backend.tools.tavily.client import TavilyClient, TavilyResponseError
from backend.tools.tavily.research import ResearchTimeoutError, run_research


def _client(handler) -> TavilyClient:
    return TavilyClient(
        api_key="tvly-test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


class TestCrawlEMap:
    @pytest.mark.asyncio
    async def test_crawl_devolve_as_paginas_varridas(self):
        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "base_url": "https://docs.test",
                    "results": [
                        {"url": "https://docs.test/a", "raw_content": "página A"}
                    ],
                },
            )

        async with _client(handler) as c:
            saida = await c.crawl("https://docs.test", max_depth=2)

        assert saida["results"][0]["url"] == "https://docs.test/a"

    @pytest.mark.asyncio
    async def test_parametros_de_crawl_vao_no_payload(self):
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            import json as _json

            capturado.update(_json.loads(req.content))
            return httpx.Response(200, json={"results": []})

        async with _client(handler) as c:
            await c.crawl(
                "https://docs.test",
                max_depth=3,
                limit=20,
                exclude_paths=["/blog/.*"],
            )

        assert capturado["max_depth"] == 3
        assert capturado["limit"] == 20
        assert capturado["exclude_paths"] == ["/blog/.*"]

    @pytest.mark.asyncio
    async def test_map_devolve_a_estrutura_de_links(self):
        def handler(_req):
            return httpx.Response(
                200, json={"base_url": "https://x.test", "results": ["/a", "/b"]}
            )

        async with _client(handler) as c:
            saida = await c.map("https://x.test")

        assert saida["results"] == ["/a", "/b"]


class TestCrawlEMapExigemAprovacao:
    def test_estao_em_require_approval(self):
        """Varrer um site inteiro gasta créditos por página e bate no alvo —
        é caro o suficiente pra pedir confirmação."""
        from backend.engine.hitl import REQUIRE_APPROVAL

        assert "web_crawl" in REQUIRE_APPROVAL
        assert "web_map" in REQUIRE_APPROVAL

    def test_busca_simples_continua_sem_hitl(self):
        """Erro/borda: HITL em `web_search`/`fetch_url` seria fricção sem
        ganho — são baratas e o agente as usa o tempo todo."""
        from backend.engine.hitl import REQUIRE_APPROVAL

        assert "web_search" not in REQUIRE_APPROVAL
        assert "fetch_url" not in REQUIRE_APPROVAL


class TestResearchAssincrono:
    @staticmethod
    def _handler(estados: list[str], *, com_resultado: bool = True) -> object:
        it = iter(estados)

        def handler(req: httpx.Request) -> httpx.Response:
            if req.method == "POST":
                return httpx.Response(
                    201, json={"request_id": "req-1", "status": "pending"}
                )
            try:
                status = next(it)
            except StopIteration:
                corpo: dict = {"request_id": "req-1", "status": "completed"}
                if com_resultado:
                    corpo["output"] = {"answer": "a resposta"}
                return httpx.Response(200, json=corpo)
            return httpx.Response(200, json={"request_id": "req-1", "status": status})

        return handler

    @pytest.mark.asyncio
    async def test_201_seguido_de_polling_ate_completed(self):
        async with _client(self._handler(["pending", "running"])) as c:
            saida = await run_research(
                c, "qual o estado da arte?", poll_interval_s=0, timeout_s=30
            )

        assert saida["output"]["answer"] == "a resposta"

    @pytest.mark.asyncio
    async def test_resposta_sem_request_id_falha_na_largada(self):
        """Erro/borda: sem id não há como acompanhar — o job rodaria e
        cobraria sem ninguém buscar o resultado."""

        def handler(_req):
            return httpx.Response(201, json={"status": "pending"})

        async with _client(handler) as c:
            with pytest.raises(TavilyResponseError, match="request_id"):
                await run_research(c, "q", poll_interval_s=0, timeout_s=30)

    @pytest.mark.asyncio
    async def test_estado_failed_vira_erro_e_nao_continua_consultando(self):
        def handler(req: httpx.Request) -> httpx.Response:
            if req.method == "POST":
                return httpx.Response(
                    201, json={"request_id": "req-1", "status": "pending"}
                )
            return httpx.Response(200, json={"status": "failed", "error": "sem fontes"})

        async with _client(handler) as c:
            with pytest.raises(TavilyResponseError, match="failed"):
                await run_research(c, "q", poll_interval_s=0, timeout_s=30)

    @pytest.mark.asyncio
    async def test_polling_que_nunca_conclui_respeita_o_teto(self):
        """Erro/borda crítico: mesma trava do vídeo do OpenRouter e do
        incidente do NATS — loop de espera sem corte gira para sempre."""
        consultas = 0

        def handler(req: httpx.Request) -> httpx.Response:
            nonlocal consultas
            if req.method == "POST":
                return httpx.Response(
                    201, json={"request_id": "req-1", "status": "pending"}
                )
            consultas += 1
            return httpx.Response(200, json={"status": "running"})

        async with _client(handler) as c:
            with pytest.raises(ResearchTimeoutError):
                await run_research(c, "q", poll_interval_s=0, timeout_s=0.05)

        assert consultas > 0, "desistiu antes de consultar uma vez"

    @pytest.mark.asyncio
    async def test_completed_sem_saida_vira_erro(self):
        """Erro/borda: concluir sem entregar a pesquisa é falha disfarçada
        de sucesso."""
        async with _client(self._handler([], com_resultado=False)) as c:
            with pytest.raises(TavilyResponseError, match="output"):
                await run_research(c, "q", poll_interval_s=0, timeout_s=30)
