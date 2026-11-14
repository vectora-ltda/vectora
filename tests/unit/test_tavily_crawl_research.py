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

from backend.tools.tavily.client import TavilyClient


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
