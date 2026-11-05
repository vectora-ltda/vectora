"""Web search/fetch do Ollama — ``POST /api/web_search`` e ``/api/web_fetch``.

Pegadinha central: **são cloud**, em ``https://ollama.com``, não no servidor
local. Exigem `OLLAMA_API_KEY` mesmo com o Ollama rodando na máquina — e a
mensagem de erro precisa dizer isso, senão vira "por que não funciona se meu
Ollama está ligado?".

Entram como mais um backend do roteador de busca, não como tool própria.
"""

from __future__ import annotations

import httpx
import pytest

from backend.llm.ollama.web_search import (
    OLLAMA_CLOUD_URL,
    ollama_web_fetch,
    ollama_web_search,
    web_search_available,
)


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestDisponibilidade:
    def test_sem_api_key_a_capacidade_nao_esta_disponivel(self, monkeypatch):
        from backend.settings import settings as _s

        monkeypatch.setattr(_s, "ollama_api_key", "", raising=False)
        assert web_search_available() is False

    def test_com_api_key_fica_disponivel(self, monkeypatch):
        from backend.settings import settings as _s

        monkeypatch.setattr(_s, "ollama_api_key", "oll-test", raising=False)
        assert web_search_available() is True


class TestWebSearch:
    @pytest.mark.asyncio
    async def test_resultados_viram_lista_normalizada(self):
        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "title": "Vectora",
                            "url": "https://vectora.chat",
                            "content": "agente self-hosted",
                        }
                    ]
                },
            )

        resultados = await ollama_web_search(
            "o que é vectora", api_key="oll-test", http_client=_client(handler)
        )

        assert resultados == [
            {
                "title": "Vectora",
                "url": "https://vectora.chat",
                "content": "agente self-hosted",
            }
        ]

    @pytest.mark.asyncio
    async def test_bate_na_cloud_e_nao_no_servidor_local(self):
        """Erro/borda que motiva o teste: apontar pro `OLLAMA_BASE_URL` local
        devolve 404 — estes endpoints só existem em ollama.com."""
        visto: dict[str, str] = {}

        def handler(req: httpx.Request) -> httpx.Response:
            visto["url"] = str(req.url)
            visto["auth"] = req.headers.get("authorization", "")
            return httpx.Response(200, json={"results": []})

        await ollama_web_search("q", api_key="oll-test", http_client=_client(handler))

        assert visto["url"].startswith(OLLAMA_CLOUD_URL)
        assert "127.0.0.1" not in visto["url"]
        assert visto["auth"] == "Bearer oll-test"

    @pytest.mark.asyncio
    async def test_max_results_acima_de_10_e_rejeitado_antes_da_chamada(self):
        """Erro/borda: o limite documentado é 10. Cortar aqui evita gastar
        uma chamada pra receber 400."""
        chamou = False

        def handler(_req):
            nonlocal chamou
            chamou = True
            return httpx.Response(200, json={"results": []})

        with pytest.raises(ValueError, match="10"):
            await ollama_web_search(
                "q", api_key="oll-test", max_results=50, http_client=_client(handler)
            )
        assert not chamou

    @pytest.mark.asyncio
    async def test_sem_api_key_erro_explica_que_e_cloud(self):
        """Erro/borda: a mensagem tem que dizer que é recurso de nuvem, senão
        o usuário com Ollama local ligado não entende a recusa."""

        def handler(_req):
            return httpx.Response(200, json={"results": []})

        with pytest.raises(ValueError, match=r"nuvem|cloud"):
            await ollama_web_search("q", api_key="", http_client=_client(handler))

    @pytest.mark.asyncio
    async def test_results_ausente_devolve_lista_vazia_sem_lancar(self):
        """Busca sem resultado é resposta válida, não falha — a tool degrada
        (CLAUDE.md regra 11)."""

        def handler(_req):
            return httpx.Response(200, json={})

        assert (
            await ollama_web_search(
                "termo improvável", api_key="oll-test", http_client=_client(handler)
            )
            == []
        )

    @pytest.mark.asyncio
    async def test_401_vira_erro_de_credencial(self):
        def handler(_req):
            return httpx.Response(401, json={"error": "invalid key"})

        with pytest.raises(RuntimeError, match="OLLAMA_API_KEY"):
            await ollama_web_search(
                "q", api_key="oll-errada", http_client=_client(handler)
            )


class TestWebFetch:
    @pytest.mark.asyncio
    async def test_devolve_titulo_conteudo_e_links(self):
        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "title": "Página",
                    "content": "corpo",
                    "links": ["https://a.test"],
                },
            )

        pagina = await ollama_web_fetch(
            "https://exemplo.test", api_key="oll-test", http_client=_client(handler)
        )

        assert pagina["title"] == "Página"
        assert pagina["content"] == "corpo"
        assert pagina["links"] == ["https://a.test"]

    @pytest.mark.asyncio
    async def test_url_vazia_nem_chama_a_api(self):
        chamou = False

        def handler(_req):
            nonlocal chamou
            chamou = True
            return httpx.Response(200, json={})

        with pytest.raises(ValueError, match="url"):
            await ollama_web_fetch("", api_key="oll-test", http_client=_client(handler))
        assert not chamou

    @pytest.mark.asyncio
    async def test_pagina_sem_conteudo_devolve_campos_vazios_sem_lancar(self):
        """Erro/borda: página que o extrator não entendeu devolve estrutura
        vazia — o agente decide o que fazer, a tool não derruba o turno."""

        def handler(_req):
            return httpx.Response(200, json={"title": "", "content": ""})

        pagina = await ollama_web_fetch(
            "https://vazia.test", api_key="oll-test", http_client=_client(handler)
        )

        assert pagina["content"] == ""
        assert pagina["links"] == []
