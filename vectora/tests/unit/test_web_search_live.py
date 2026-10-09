"""``web_search``/``fetch_url`` (``backend/tools/web.py``) contra o Tavily
real. Sem mock: cada teste chama a API do Tavily de verdade e consome quota
real da chave configurada em ``~/.vectora/.env``.

Guardado pelo marker ``live`` (só via ``scons tests-live``) e por
``settings.tavily_api_key`` truthy — mesmo padrão de guard que
``backend/tools/web.py:161`` já usa para decidir entre Tavily e o fallback
via DuckDuckGo/Chromium.

Complementa (não duplica) ``test_browser_search_fallback_real.py`` — aquele
arquivo testa o caminho SEM Tavily (fallback DuckDuckGo/Chromium); este
testa especificamente o caminho COM Tavily configurado.
"""

from __future__ import annotations

import json

import pytest

from backend.settings import settings
from backend.tools.web import fetch_url, web_search

pytestmark = [
    pytest.mark.live,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not settings.tavily_api_key,
        reason="TAVILY_API_KEY não configurado em ~/.vectora/.env",
    ),
]


def _results(raw: str) -> list[dict]:
    data = json.loads(raw)
    assert isinstance(data, list), f"esperava lista de resultados, veio: {data!r}"
    return data


# ---------------------------------------------------------------------------
# web_search — queries reais, estrutura da resposta
# ---------------------------------------------------------------------------


async def test_web_search_query_generica_real():
    raw = await web_search(query="FastAPI framework")
    results = _results(raw)
    assert len(results) > 0
    for r in results:
        assert r.get("url")
        assert r.get("title") is not None
        assert r.get("content") is not None


async def test_web_search_query_local_com_time_range_real():
    raw = await web_search(
        query="clima em São Paulo hoje", topic="general", time_range="day"
    )
    results = _results(raw)
    assert len(results) > 0


async def test_web_search_query_tecnica_especifica_real():
    raw = await web_search(query="Python asyncio event loop internals")
    results = _results(raw)
    assert len(results) > 0
    assert any("content" in r and r["content"] for r in results)


async def test_web_search_topic_finance_real():
    raw = await web_search(query="Nvidia stock price", topic="finance")
    results = _results(raw)
    assert len(results) > 0


async def test_web_search_com_include_domains_real():
    raw = await web_search(query="python asyncio", include_domains=["github.com"])
    results = _results(raw)
    # Tavily pode devolver menos resultados quando restrito a um domínio —
    # o importante é que nenhum resultado escape do domínio pedido.
    for r in results:
        assert "github.com" in r.get("url", "")


async def test_web_search_com_exclude_domains_real():
    raw = await web_search(
        query="python asyncio tutorial", exclude_domains=["github.com"]
    )
    results = _results(raw)
    for r in results:
        assert "github.com" not in r.get("url", "")


async def test_web_search_query_vazia_borda():
    # Par de erro/borda: query vazia não deve derrubar a tool nem propagar
    # exceção — o contrato da tool (nunca lançar) tem que se manter.
    raw = await web_search(query="")
    data = json.loads(raw)
    assert isinstance(data, (list, dict))
    if isinstance(data, dict):
        assert "error" in data or "status" in data


# ---------------------------------------------------------------------------
# fetch_url — extração de conteúdo real
# ---------------------------------------------------------------------------


async def test_fetch_url_pagina_real() -> None:
    content = await fetch_url(url="https://fastapi.tiangolo.com/")
    assert isinstance(content, str)
    assert content.strip()
    assert not content.startswith("Error:")


async def test_fetch_url_github_publico_real() -> None:
    """A URL pública do GitHub deve ser legível mesmo sem depender do Tavily."""
    content = await fetch_url(url="https://github.com/vectora-ltda/vectora/issues/317")
    assert content.strip()
    assert not content.startswith("Error:")
    body = content.split("\n", 2)[-1].rsplit("\n</untrusted_content>", 1)[0]
    assert "provider" in body.lower() or "llama" in body.lower()


async def test_fetch_url_url_invalida_sem_lancar():
    # Borda: URL sem esquema não bate rede nenhuma — validação local, mas
    # ainda cobre o contrato "nunca lança, sempre devolve string de erro".
    result = await fetch_url(url="not-a-real-url")
    assert result.startswith("Error:")


async def test_fetch_url_pagina_inexistente_real_nao_lanca():
    # Domínio real, path que garantidamente não existe — extração real
    # falha, mas a tool deve degradar para texto de erro, nunca propagar.
    content = await fetch_url(
        url="https://fastapi.tiangolo.com/este-path-nao-existe-vectora-test-404"
    )
    assert isinstance(content, str)
