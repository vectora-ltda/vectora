"""Fallback sem API key de `web_search`/`fetch_url` (`backend/tools/web.py`)
quando `TAVILY_API_KEY` está ausente ou a chamada ao Tavily falha.

`search_fallback` usa a API JSON oficial do DuckDuckGo (Instant Answer,
`api.duckduckgo.com`) — verificado empiricamente nesta sessão: o endpoint de
scraping HTML (`html.duckduckgo.com/html/`) devolve um desafio anti-bot
("select all squares containing a duck") mesmo com Chromium real via
Playwright e headers de browser legítimo — não é fingerprint do Chromium
especificamente, um `httpx` puro leva o mesmo bloqueio, então é rejeição por
IP/rede, algo que afetaria igualmente ambientes de nuvem/VPS. A API JSON é
oficial, sem chave e não tropeça nesse bloqueio.

`fetch_fallback` conecta ao Chromium do Electron via CDP — extrair o texto de
uma URL específica reutiliza a sessão autenticada do Browser Workbench.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_DUCKDUCKGO_API_URL = "https://api.duckduckgo.com/"
_HTTP_TIMEOUT_S = 10.0
_NAV_TIMEOUT_MS = 10_000

_browser: Any = None
_playwright: Any = None


def _get_browser() -> Any:
    global _browser, _playwright
    if _browser is None:
        from playwright.sync_api import sync_playwright

        from backend.browser.cdp import electron_cdp_endpoint

        started_playwright: Any = None
        try:
            endpoint = electron_cdp_endpoint()
            started_playwright = sync_playwright().start()
            _playwright = started_playwright
            _browser = _playwright.chromium.connect_over_cdp(endpoint)
            if not _browser.contexts:
                raise RuntimeError("Chromium do Electron não expôs contexto CDP")
        except Exception:
            if started_playwright is not None:
                try:
                    started_playwright.stop()
                except Exception:
                    logger.exception("search_fallback_playwright_cleanup_failed")
            _browser = None
            _playwright = None
            raise
        logger.info("electron_browser_fallback_connected")
    return _browser


def close_search_fallback_browser() -> None:
    """Fecha o Chromium do fallback, se estiver aberto. Idempotente."""
    global _browser, _playwright
    if _browser is None and _playwright is None:
        return
    try:
        if _playwright is not None:
            _playwright.stop()
    except Exception:
        logger.exception("search_fallback_browser_close_failed")
    finally:
        _browser = None
        _playwright = None


def search_fallback(query: str, max_results: int = 5) -> list[dict[str, str]]:
    """Busca `query` na API Instant Answer do DuckDuckGo (sem chave).

    Levanta em caso de falha de rede — o caller decide o fallback textual,
    não engolimos erro aqui (a tool que chama já tem `try/except`).
    """
    import httpx

    response = httpx.get(
        _DUCKDUCKGO_API_URL,
        params={"q": query, "format": "json", "no_html": 1},
        timeout=_HTTP_TIMEOUT_S,
    )
    response.raise_for_status()
    data = response.json()

    results: list[dict[str, str]] = []
    if data.get("AbstractText"):
        results.append(
            {
                "title": data.get("Heading", query),
                "content": data["AbstractText"],
                "url": data.get("AbstractURL", ""),
            }
        )
    for topic in data.get("RelatedTopics", []):
        if len(results) >= max_results:
            break
        text = topic.get("Text")
        if not text:
            continue
        results.append(
            {
                "title": text.split(" - ", 1)[0],
                "content": text,
                "url": topic.get("FirstURL", ""),
            }
        )

    logger.info(
        "search_fallback_completed",
        extra={"query": query, "num_results": len(results)},
    )
    return results


def fetch_fallback(url: str) -> str:
    """Navega até `url` num Chromium real e retorna o texto visível da
    página. Levanta em caso de falha — mesmo contrato de `search_fallback`.
    """
    from backend.browser.ssrf_guard import is_url_ssrf_safe

    if not is_url_ssrf_safe(url):
        raise ValueError(
            f"URL recusada: {url!r} resolve para um IP privado/loopback/"
            "link-local/metadata — bloqueado por segurança (SSRF)."
        )

    browser = _get_browser()
    if not browser.contexts:
        raise RuntimeError("Chromium do Electron não expôs contexto CDP")
    page = browser.contexts[0].new_page()
    try:
        page.set_default_timeout(_NAV_TIMEOUT_MS)
        page.goto(url)
        text = page.inner_text("body")
        logger.info(
            "fetch_fallback_completed",
            extra={"url": url, "content_length": len(text)},
        )
        return text
    finally:
        page.close()
