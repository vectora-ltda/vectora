"""Fallback de busca sem API key contra serviços reais.

A API JSON do DuckDuckGo é usada por `search_fallback`; `fetch_fallback`
conecta ao Chromium já ativo do Electron via CDP.
"""

from __future__ import annotations

import os

import httpx
import pytest

from backend.browser import search_fallback


def _electron_cdp_available() -> bool:
    endpoint = os.environ.get("VECTORA_ELECTRON_CDP_URL", "").strip()
    if not endpoint:
        port = os.environ.get("VECTORA_ELECTRON_CDP_PORT", "9223").strip()
        if port.isdigit():
            endpoint = f"http://127.0.0.1:{port}"
    if not endpoint:
        return False
    try:
        return httpx.get(f"{endpoint}/json/version", timeout=1).is_success
    except Exception:
        return False


# search_fallback() chama httpx.get() direto contra a API JSON do
# DuckDuckGo — sem Chromium nenhum. `browser` é só pra testes que conectam
# ao Electron via CDP (ver descrição do marker em pyproject.toml);
# aplicado no módulo inteiro antes, isso fazia os 2 testes de
# search_fallback (rede real de terceiro, sem mock) escaparem do filtro
# `not live` que a CI já usa pra isolar esse tipo de teste do gate
# principal — achado real: ConnectTimeout intermitente do runner do
# GitHub Actions pro DuckDuckGo derrubava o job inteiro. Marker agora
# vai por função: `live` nos 2 testes de search_fallback, `browser` só
# nos 2 de fetch_fallback (que de fato precisam de Chromium).


@pytest.fixture(autouse=True)
def _clean_fallback_browser():
    """Garante nenhum Chromium do fallback sobrando de um teste anterior."""
    search_fallback.close_search_fallback_browser()
    yield
    search_fallback.close_search_fallback_browser()


@pytest.mark.live
def test_search_fallback_retorna_resultados_reais_do_duckduckgo():
    results = search_fallback.search_fallback("python programming language")

    assert len(results) > 0
    for r in results:
        assert r["title"]
        assert "url" in r
        assert "content" in r


@pytest.mark.live
def test_search_fallback_query_sem_instant_answer_retorna_lista_vazia_sem_lancar():
    # Par de erro/borda: query sem AbstractText/RelatedTopics (ex.: string
    # aleatória sem entrada na base de instant-answers) não deve lançar —
    # só retorna lista vazia, e o caller trata isso normalmente.
    results = search_fallback.search_fallback("asdkjqwoiehjqwoiuhASDLKJQWEOIU9812739")
    assert results == []


@pytest.mark.browser
@pytest.mark.skipif(
    not _electron_cdp_available(),
    reason="Electron não está ativo ou não publicou o endpoint CDP",
)
def test_fetch_fallback_extrai_texto_visivel_de_uma_pagina_real():
    text = search_fallback.fetch_fallback("https://example.com")

    assert "Example Domain" in text


def test_fetch_fallback_url_invalida_levanta_em_vez_de_retornar_string_vazia():
    # Par de erro: URL inexistente/malformada deve levantar (o caller —
    # backend/tools/web.py — é quem decide degradar pro erro textual),
    # nunca retornar silenciosamente uma string vazia.
    #
    # `.invalid` é um TLD reservado (RFC 2606) que nunca resolve em DNS
    # nenhum — então `ssrf_guard.is_url_ssrf_safe` (fail-closed por design:
    # falha de resolução também é tratada como não-seguro, ver seu
    # docstring) sempre recusa essa URL ANTES de chegar no Chromium — sem
    # este teste nunca toca o Chromium de verdade, então continua executável
    # quando o Electron não está ativo.
    with pytest.raises(ValueError, match="SSRF"):
        search_fallback.fetch_fallback(
            "https://este-dominio-nao-existe-de-verdade.invalid"
        )
