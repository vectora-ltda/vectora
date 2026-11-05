"""Cliente HTTP nativo do Tavily — ``/search`` e ``/extract``.

A API do Tavily tem seis endpoints: `/search`, `/extract`, `/crawl`,
`/map`, `/research` e `/usage`.

O contrato de saída das tools (`web_search` devolve `json.dumps(results)`)
**não muda**: é o contrato com o LLM.
"""

from __future__ import annotations

import httpx
import pytest

from backend.tools.tavily.client import (
    TavilyAuthError,
    TavilyClient,
    TavilyQuotaError,
    TavilyResponseError,
)


def _client(handler) -> TavilyClient:
    return TavilyClient(
        api_key="tvly-test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


class TestAuth:
    @pytest.mark.asyncio
    async def test_manda_bearer_com_o_prefixo_tvly(self):
        visto: dict[str, str] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            visto["auth"] = req.headers.get("authorization", "")
            return httpx.Response(200, json={"results": []})

        async with _client(handler) as c:
            await c.search("q")

        assert visto["auth"] == "Bearer tvly-test"

    def test_sem_api_key_falha_na_construcao(self):
        """Erro/borda: key vazia tem que falhar aqui, não render 401 confuso
        lá na frente."""
        with pytest.raises(TavilyAuthError, match="TAVILY_API_KEY"):
            TavilyClient(api_key="")

    @pytest.mark.asyncio
    async def test_401_e_432_viram_erros_distintos(self):
        """Chave inválida e cota estourada exigem ações diferentes do
        usuário, e hoje viram a mesma mensagem genérica."""

        def handler_401(_req):
            return httpx.Response(401, json={"detail": "unauthorized"})

        def handler_429(_req):
            return httpx.Response(429, json={"detail": "rate limited"})

        async with _client(handler_401) as c:
            with pytest.raises(TavilyAuthError):
                await c.search("q")

        async with _client(handler_429) as c:
            with pytest.raises(TavilyQuotaError):
                await c.search("q")


class TestSearch:
    @pytest.mark.asyncio
    async def test_resultados_saem_na_forma_esperada_pelas_tools(self):
        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Vectora",
                            "url": "https://vectora.chat",
                            "content": "resumo",
                            "score": 0.9,
                        }
                    ]
                },
            )

        async with _client(handler) as c:
            resultados = await c.search("vectora")

        assert resultados[0]["url"] == "https://vectora.chat"
        assert resultados[0]["title"] == "Vectora"

    @pytest.mark.asyncio
    async def test_parametros_por_chamada_e_nao_por_instanciacao(self):
        """`search_depth`/`max_results` são parâmetros por chamada, não
        presos na criação do client."""
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            import json as _json

            capturado.update(_json.loads(req.content))
            return httpx.Response(200, json={"results": []})

        async with _client(handler) as c:
            await c.search(
                "q",
                topic="news",
                time_range="week",
                max_results=3,
                search_depth="advanced",
                include_domains=["github.com"],
            )

        assert capturado["topic"] == "news"
        assert capturado["time_range"] == "week"
        assert capturado["max_results"] == 3
        assert capturado["search_depth"] == "advanced"
        assert capturado["include_domains"] == ["github.com"]

    @pytest.mark.asyncio
    async def test_filtro_nao_informado_fica_ausente_do_payload(self):
        """Erro/borda: mandar `include_domains: null` restringe a busca em
        alguns casos em vez de deixar aberta."""
        capturado: dict = {}

        def handler(req: httpx.Request) -> httpx.Response:
            import json as _json

            capturado.update(_json.loads(req.content))
            return httpx.Response(200, json={"results": []})

        async with _client(handler) as c:
            await c.search("q")

        assert "include_domains" not in capturado
        assert "time_range" not in capturado

    @pytest.mark.asyncio
    async def test_busca_sem_resultado_devolve_lista_vazia_sem_lancar(self):
        def handler(_req):
            return httpx.Response(200, json={"results": []})

        async with _client(handler) as c:
            assert await c.search("termo improvável") == []

    @pytest.mark.asyncio
    async def test_resposta_sem_results_vira_erro_tipado(self):
        """Erro/borda: `results` ausente é resposta malformada, diferente de
        `results: []` — confundir os dois esconde falha de contrato."""

        def handler(_req):
            return httpx.Response(200, json={"query": "q"})

        async with _client(handler) as c:
            with pytest.raises(TavilyResponseError, match="results"):
                await c.search("q")


class TestExtract:
    @pytest.mark.asyncio
    async def test_extrai_conteudo_das_urls(self):
        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "results": [{"url": "https://a.test", "raw_content": "conteúdo"}],
                    "failed_results": [],
                },
            )

        async with _client(handler) as c:
            extraidos = await c.extract(["https://a.test"])

        assert extraidos[0]["raw_content"] == "conteúdo"

    @pytest.mark.asyncio
    async def test_url_que_falhou_nao_some_em_silencio(self):
        """Erro/borda: o Tavily separa `failed_results`. Ignorar faz o agente
        achar que a página estava vazia, em vez de saber que não foi lida."""

        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "results": [],
                    "failed_results": [
                        {"url": "https://morta.test", "error": "timeout"}
                    ],
                },
            )

        async with _client(handler) as c:
            with pytest.raises(TavilyResponseError, match=r"morta\.test"):
                await c.extract(["https://morta.test"])

    @pytest.mark.asyncio
    async def test_lista_de_urls_vazia_nem_chama_a_api(self):
        chamou = False

        def handler(_req):
            nonlocal chamou
            chamou = True
            return httpx.Response(200, json={"results": []})

        async with _client(handler) as c:
            assert await c.extract([]) == []
        assert not chamou


class TestUsage:
    @pytest.mark.asyncio
    async def test_usage_devolve_consumo_da_key_e_do_plano(self):
        """`GET /usage` — endpoint que o medidor de consumo usa para
        reportar o gasto da key/plano."""

        def handler(req: httpx.Request) -> httpx.Response:
            assert req.method == "GET"
            return httpx.Response(
                200,
                json={
                    "key": {"usage": 120, "limit": 1000, "search_usage": 100},
                    "account": {
                        "current_plan": "researcher",
                        "plan_usage": 500,
                        "plan_limit": 4000,
                    },
                },
            )

        async with _client(handler) as c:
            uso = await c.usage()

        assert uso["key"]["usage"] == 120
        assert uso["account"]["current_plan"] == "researcher"

    @pytest.mark.asyncio
    async def test_usage_com_401_vira_erro_de_credencial(self):
        def handler(_req):
            return httpx.Response(401, json={"detail": "bad key"})

        async with _client(handler) as c:
            with pytest.raises(TavilyAuthError):
                await c.usage()
