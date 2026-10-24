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
import threading
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from playwright.sync_api import Browser, Page

_DUCKDUCKGO_API_URL = "https://api.duckduckgo.com/"
_HTTP_TIMEOUT_S = 10.0
_NAV_TIMEOUT_MS = 10_000

_browser: Any = None
_playwright: Any = None
_BROWSER_LOCK = threading.RLock()


def _get_browser() -> Any:
    """Connect to the Electron Chromium instance and cache the connection."""
    global _browser, _playwright
    if _browser is None:
        from playwright.sync_api import sync_playwright

        from backend.browser.cdp import electron_cdp_endpoint, electron_cdp_headers

        started_playwright: Any = None
        try:
            endpoint = electron_cdp_endpoint()
            started_playwright = sync_playwright().start()
            _playwright = started_playwright
            headers = electron_cdp_headers()
            if headers:
                _browser = _playwright.chromium.connect_over_cdp(
                    endpoint, headers=headers
                )
            else:
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


def _install_ssrf_route(page: Page, policy: Any = None) -> Any:
    """Block unsafe browser requests, including redirects and subresources."""
    from backend.browser.ssrf_guard import is_url_ssrf_safe
    from backend.services.egress_policy import EgressPolicy, is_url_allowed

    if isinstance(policy, dict):
        policy = EgressPolicy.model_validate(policy)

    def _handle(route: Any) -> None:
        """Abort requests that violate the run egress policy."""
        request_url = str(route.request.url)
        scheme = request_url.split(":", 1)[0].lower()
        allowed = True
        if scheme in {"http", "https"}:
            allowed = (
                is_url_allowed(request_url, policy)[0]
                if isinstance(policy, EgressPolicy)
                else is_url_ssrf_safe(request_url)
            )
        if not allowed:
            route.abort("blockedbyclient")
            return
        route.continue_()

    page.route("**/*", _handle)
    return _handle


def _remove_ssrf_route(page: Page, handler: Any) -> None:
    """Remove a temporary SSRF route without masking the navigation result."""
    try:
        page.unroute("**/*", handler)
    except Exception:
        logger.debug("browser SSRF route cleanup failed", exc_info=True)


def browser_search(
    query: str,
    max_results: int = 5,
    *,
    workspace_id: str | None = None,
    egress_policy: Any = None,
) -> list[dict[str, str]]:
    """Search through the Electron Chromium session.

    This deliberately reuses an existing Browser Workbench page. Electron's
    embedded CDP proxy does not implement ``Target.createTarget``; opening a
    hidden Playwright page here would either fail or create a second browser
    runtime. The visible page is therefore the authoritative navigation
    surface and failures are returned to the provider for a clear diagnostic.
    """
    if not query.strip():
        return []
    with _BROWSER_LOCK:
        browser = _get_browser()
        page = _find_workbench_page(browser, workspace_id=workspace_id)
        route = _install_ssrf_route(page, egress_policy)
        page.set_default_timeout(_NAV_TIMEOUT_MS)
        try:
            page.goto(
                "https://www.bing.com/search?q=" + quote_plus(query),
                wait_until="domcontentloaded",
            )
            from backend.browser.ssrf_guard import is_url_ssrf_safe

            final_url = str(page.url)
            if not is_url_ssrf_safe(final_url):
                raise ValueError("redirect recusado pelo SSRF guard")
            rows = page.locator("li.b_algo")
            results: list[dict[str, str]] = []
            for index in range(min(rows.count(), max_results)):
                row = rows.nth(index)
                anchor = row.locator("h2 a").first
                if anchor.count() == 0:
                    continue
                title = anchor.inner_text().strip()
                url = anchor.get_attribute("href") or ""
                content = ""
                snippet = row.locator(".b_caption p").first
                if snippet.count():
                    content = snippet.inner_text().strip()
                if url:
                    results.append(
                        {
                            "title": title,
                            "content": content,
                            "url": url,
                            "provider": "browser",
                        }
                    )
        finally:
            _remove_ssrf_route(page, route)
    logger.info(
        "browser_search_completed",
        extra={"query": query, "num_results": len(results)},
    )
    return results


def _find_workbench_page(browser: Browser, *, workspace_id: str | None = None) -> Page:
    """Return a user-visible page and reject the Vectora shell target."""
    contexts = list(browser.contexts)
    if not contexts:
        raise RuntimeError("Chromium do Electron não expôs contexto CDP")
    pages = list(contexts[0].pages)
    candidates = [
        page
        for page in pages
        if isinstance(getattr(page, "url", None), str)
        and not str(page.url).startswith(
            ("file://", "devtools://", "vectora-app://", "vscode://")
        )
        and "127.0.0.1:8080" not in str(page.url)
        and "localhost:8080" not in str(page.url)
    ]
    if workspace_id is not None:
        from backend.browser.session import _page_owners, _page_target_id

        owned = []
        for page in candidates:
            try:
                owner = _page_owners.get(_page_target_id(page))
            except Exception:
                owner = None
            if owner == workspace_id:
                owned.append(page)
        candidates = owned
    if not candidates:
        raise RuntimeError(
            "Nenhuma aba do Browser Workbench está aberta; abra uma aba no Electron "
            "antes de usar o provider browser"
        )
    return candidates[0]


def fetch_fallback(
    url: str, *, workspace_id: str | None = None, egress_policy: Any = None
) -> str:
    """Navega até `url` num Chromium real e retorna o texto visível da
    página. Levanta em caso de falha — mesmo contrato de `search_fallback`.
    """
    from backend.browser.ssrf_guard import is_url_ssrf_safe

    if not is_url_ssrf_safe(url):
        raise ValueError(
            f"URL recusada: {url!r} resolve para um IP privado/loopback/"
            "link-local/metadata — bloqueado por segurança (SSRF)."
        )

    with _BROWSER_LOCK:
        browser = _get_browser()
        if not browser.contexts:
            raise RuntimeError("Chromium do Electron não expôs contexto CDP")
        page = _find_workbench_page(browser, workspace_id=workspace_id)
        route = _install_ssrf_route(page, egress_policy)
        try:
            page.set_default_timeout(_NAV_TIMEOUT_MS)
            page.goto(url)
            final_url = str(page.url)
            if not is_url_ssrf_safe(final_url):
                raise ValueError("redirect recusado pelo SSRF guard")
            text = page.inner_text("body")
            logger.info(
                "fetch_fallback_completed",
                extra={"url": final_url, "content_length": len(text)},
            )
            return text
        finally:
            _remove_ssrf_route(page, route)


def browser_map(
    url: str,
    limit: int = 50,
    *,
    workspace_id: str | None = None,
    egress_policy: Any = None,
) -> list[str]:
    """Return normalized links from a page using the shared BrowserView."""
    from urllib.parse import urljoin, urlparse

    from backend.browser.ssrf_guard import is_url_ssrf_safe

    if not is_url_ssrf_safe(url):
        raise ValueError("URL recusada pelo SSRF guard")
    with _BROWSER_LOCK:
        browser = _get_browser()
        page = _find_workbench_page(browser, workspace_id=workspace_id)
        route = _install_ssrf_route(page, egress_policy)
        try:
            page.set_default_timeout(_NAV_TIMEOUT_MS)
            page.goto(url, wait_until="domcontentloaded")
            final_url = str(page.url)
            if not is_url_ssrf_safe(final_url):
                raise ValueError("redirect recusado pelo SSRF guard")
            origin = urlparse(final_url)
            links: list[str] = []
            for href in page.locator("a").evaluate_all(
                "els => els.map(el => el.href).filter(Boolean)"
            ):
                normalized = urljoin(final_url, str(href).split("#", 1)[0])
                parsed = urlparse(normalized)
                if parsed.scheme not in {"http", "https"}:
                    continue
                if parsed.netloc != origin.netloc or not is_url_ssrf_safe(normalized):
                    continue
                if normalized not in links:
                    links.append(normalized)
                if len(links) >= limit:
                    break
            return links
        finally:
            _remove_ssrf_route(page, route)
