"""Web tools: busca e extração de conteúdo da internet via Tavily.

Cliente HTTP nativo (`backend/tools/tavily/`), não o pacote de integração de
terceiros: aquele cobria só `/search` e `/extract` e prendia
`search_depth`/`max_results` na instanciação.

As tools `web_search` e `fetch_url` preservam nome e formato de saída —
JSON list para `web_search`, texto puro para `fetch_url`. Esse é o contrato
com o LLM e não muda.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from typing import Any, Literal
from urllib.parse import urljoin, urlparse

import httpx

from backend.settings import settings
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)

#: `max_results` e `search_depth` agora são por chamada — o cliente nativo
#: não os prende na construção como o pacote de integração de terceiros fazia.
_MAX_RESULTS = 5


def _tavily_client() -> Any:
    """Cliente Tavily novo por chamada — barato (só monta o httpx sob demanda)
    e evita segurar conexão entre turnos do agente."""
    from backend.tools.tavily.client import TavilyClient

    return TavilyClient(api_key=settings.tavily_api_key or "")


def _get_search_tool() -> Any:
    """Retorna o backend resolvido para busca.

    Mantido como ponto de extensão para testes e mocks.
    """
    from backend.tools.search_registry import resolve_backend

    return resolve_backend()


def _get_extract_tool() -> Any:
    """Retorna o backend resolvido para extração de URL."""
    from backend.tools.search_registry import resolve_backend

    return resolve_backend()


async def _invoke_backend(tool: Any, payload: dict[str, Any]) -> Any:
    """Executa um backend de teste ou produção com a interface disponível.

    Alguns backends expõem `.search()`/`.extract()`, enquanto os mocks de
    teste usam `.invoke()`. Este helper aceita ambos sem quebrar o contrato
    externo das tools — o resultado é aguardado só quando o backend
    devolveu algo awaitable (produção); mocks síncronos passam direto.
    """
    if hasattr(tool, "invoke"):
        result = tool.invoke(payload)
    elif "url" in payload and hasattr(tool, "extract"):
        result = tool.extract([payload["url"]], extract_depth="advanced")
    elif "query" in payload and hasattr(tool, "search"):
        result = tool.search(payload["query"])
    else:
        raise AttributeError("backend tool does not expose invoke/search/extract")
    if inspect.isawaitable(result):
        return await result
    return result


def _is_quota_error(err: str) -> bool:
    """Retorna True se a mensagem de erro indica quota/rate-limit do Tavily."""
    el = err.lower()
    return (
        "429" in err or "too many requests" in el or "quota" in el or "rate limit" in el
    )


async def _search_via_fallback(query: str) -> str:
    """Fallback sem API key: API JSON oficial do DuckDuckGo (sem chave).

    Nunca propaga — qualquer falha (rede, DNS) vira o mesmo erro textual
    que já existia sem Tavily configurado.
    """
    try:
        from backend.browser.search_fallback import search_fallback

        results = await asyncio.to_thread(search_fallback, query)
        logger.info(
            "web_search fallback completed",
            extra={"query": query, "num_results": len(results)},
        )
        return json.dumps(results)
    except Exception:
        logger.exception("web_search fallback failed", extra={"query": query})
        return json.dumps(
            {
                "status": "error",
                "error": (
                    "TAVILY_API_KEY not configured and the fallback search "
                    "failed (network error). Set TAVILY_API_KEY to retry via "
                    "Tavily."
                ),
            }
        )


async def _fetch_via_fallback(url: str) -> str:
    """Fallback sem API key para `fetch_url`: Chromium do Electron via CDP.
    Mesmo contrato de `_search_via_fallback` — nunca propaga."""
    try:
        from backend.browser.search_fallback import fetch_fallback
        from backend.services.prompt_injection import (
            detect_injection,
            envelope_untrusted,
        )

        content = await asyncio.to_thread(fetch_fallback, url)
        logger.info(
            "fetch_url fallback completed",
            extra={"url": url, "content_length": len(content)},
        )
        if (pattern := detect_injection(content)) is not None:
            logger.warning(
                "fetch_url: padrão de prompt injection detectado (log-only)",
                extra={"url": url, "pattern": pattern},
            )
        return envelope_untrusted(content, source=url)
    except Exception:
        logger.exception("fetch_url fallback failed", extra={"url": url})
        return (
            "Error: TAVILY_API_KEY not configured and the Chromium fallback "
            "failed. Set TAVILY_API_KEY and start the Electron Browser Workbench."
        )


async def _fetch_via_http(url: str, ctx: ToolContext | None = None) -> str:
    """Busca uma URL diretamente quando o backend de extração falha."""
    from backend.browser.ssrf_guard import is_url_ssrf_safe
    from backend.services.prompt_injection import detect_injection, envelope_untrusted

    headers = {
        "User-Agent": "Vectora/1.0",
        "Accept": "text/html,application/json,text/plain",
    }
    github_hosts = {"github.com", "www.github.com", "api.github.com"}
    host = (urlparse(url).hostname or "").lower()
    token: str | None = None
    if urlparse(url).scheme == "https" and host in github_hosts and ctx is not None:
        try:
            from backend.tools.github import _github_token

            token = await _github_token(ctx)
            if token:
                headers["Authorization"] = f"Bearer {token}"
        except Exception:
            logger.debug("fetch_url: token GitHub indisponível", exc_info=True)

    max_bytes = 2_000_000

    async def _read_limited(response: httpx.Response) -> str:
        response.raise_for_status()
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > max_bytes:
                remaining = max_bytes - (size - len(chunk))
                if remaining > 0:
                    chunks.append(chunk[:remaining])
                raise ValueError("resposta excede o limite de 2 MB")
            chunks.append(chunk)
        return b"".join(chunks).decode(response.encoding or "utf-8", errors="replace")

    async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
        async with client.stream("GET", url, headers=headers) as response:
            if 300 <= response.status_code < 400:
                location = response.headers.get("location")
                if not location:
                    response.raise_for_status()
                redirected = urljoin(url, location)
                if not is_url_ssrf_safe(redirected):
                    raise ValueError("redirect recusado pelo SSRF guard")
                redirected_url = urlparse(redirected)
                redirected_host = (redirected_url.hostname or "").lower()
                redirected_headers = dict(headers)
                if (
                    redirected_url.scheme != "https"
                    or redirected_host not in github_hosts
                ):
                    redirected_headers.pop("Authorization", None)
                async with client.stream(
                    "GET", redirected, headers=redirected_headers
                ) as redirected_response:
                    content = await _read_limited(redirected_response)
            else:
                content = await _read_limited(response)
    if (pattern := detect_injection(content)) is not None:
        logger.warning(
            "fetch_url: padrão de prompt injection detectado (log-only)",
            extra={"url": url, "pattern": pattern},
        )
    logger.info(
        "fetch_url direct HTTP completed",
        extra={
            "url": url,
            "content_length": len(content),
            "status": response.status_code,
        },
    )
    return envelope_untrusted(content, source=url)


@vtool(
    extras=ToolExtras(
        render_hint="search_results",
        category="web",
        destructive=False,
        icon="globe",
    )
)
async def web_search(
    query: str,
    topic: Literal["general", "news", "finance"] = "general",
    time_range: Literal["day", "week", "month", "year"] | None = None,
    include_domains: list[str] | None = None,
    exclude_domains: list[str] | None = None,
) -> str:
    """Busca a web por informações atuais usando Tavily (otimizado para agentes).

    Tavily retorna resultados estruturados, prontos para RAG, com conteúdo já extraído.

    Args:
        query: String da consulta de busca
        topic: "general" (padrão), "news" (notícias) ou "finance" (finanças)
        time_range: filtro temporal opcional — "day", "week", "month" ou "year"
        include_domains: restringe a busca a estes domínios (ex: ["github.com"]).
            Use quando souber a fonte canônica — reduz ruído e contaminação.
        exclude_domains: exclui estes domínios dos resultados

    Returns:
        JSON com lista de resultados (url, title, content, raw_content) prontos
        para embedding, ou um objeto JSON com status de erro
    """
    # O roteador resolve escolha explícita do usuário e ordem de preferência
    # (`backend/tools/search_registry.py`); o caminho Tavily abaixo só roda
    # quando ele elege o Tavily. Sem chave nenhuma o roteador devolve o
    # DuckDuckGo, mantendo o comportamento histórico.
    try:
        from backend.tools.search_registry import SearchBackendUnavailableError

        backend = _get_search_tool()
    except SearchBackendUnavailableError as exc:
        # Backend escolhido sem credencial: erro claro em vez de cair noutro
        # em silêncio — o usuário pediu aquele especificamente.
        return json.dumps({"status": "error", "error": str(exc)})

    backend_name = getattr(backend, "name", "tavily")
    if backend_name != "tavily":
        try:
            resultados = await _invoke_backend(backend, {"query": query})
            logger.info(
                "web_search via backend alternativo",
                extra={"query": query, "backend": backend_name},
            )
            return json.dumps(resultados)
        except Exception as exc:
            logger.exception(
                "web_search backend falhou", extra={"backend": backend_name}
            )
            return json.dumps({"status": "error", "error": str(exc)})

    logger.info("web_search tool called", extra={"query": query, "topic": topic})

    t0 = time.perf_counter()
    try:
        from backend.persistence.tracer import tracer as _tracer
    except Exception:
        _tracer = None  # type: ignore[assignment]

    try:
        client = _tavily_client()
        results = await client.search(
            query,
            topic=topic,
            time_range=time_range,
            include_domains=include_domains,
            exclude_domains=exclude_domains,
            max_results=_MAX_RESULTS,
            search_depth="advanced",
            include_raw_content=True,
        )
        n_results = len(results)

        logger.info(
            "web_search completed",
            extra={"query": query, "num_results": n_results},
        )
        if _tracer:
            _tracer.record_sync(
                "web_search",
                "call",
                time.perf_counter() - t0,
                {"query": query[:120], "n_results": n_results},
            )
        from backend.services.prompt_injection import detect_injection

        combined = " ".join(str(r.get("content", "")) for r in results)
        if (pattern := detect_injection(combined)) is not None:
            logger.warning(
                "web_search: padrão de prompt injection detectado (log-only)",
                extra={"query": query, "pattern": pattern},
            )
        return json.dumps(results)
    except Exception as e:
        err = str(e)
        logger.exception("web_search failed", extra={"query": query})
        if _tracer:
            _tracer.record_sync(
                "web_search",
                "call",
                time.perf_counter() - t0,
                {"query": query[:120]},
                status="error",
            )
        if _is_quota_error(err):
            logger.warning(
                "Tavily quota/rate limit atingido — usando fallback via Chromium",
                extra={"query": query},
            )
            return await _search_via_fallback(query)
        # TavilySearch levanta ToolException quando não há resultados —
        # para o cascading isso é uma lista vazia, não um erro.
        if "no search results found" in err.lower():
            return json.dumps([])
        return json.dumps(
            {
                "status": "error",
                "error": "Web search failed. Please try again.",
            }
        )


@vtool(
    extras=ToolExtras(
        render_hint="code_block",
        category="web",
        destructive=False,
        icon="link",
    )
)
async def fetch_url(url: str, ctx: ToolContext | None = None) -> str:
    """Busca e extrai conteúdo de texto de uma URL específica usando Tavily.

    Args:
        url: URL para buscar (deve começar com http:// ou https://)

    Returns:
        Conteúdo de texto extraído da página
    """
    if not url.startswith(("http://", "https://")):
        logger.warning("fetch_url called with invalid URL", extra={"url": url})
        return "Error: URL must start with http:// or https://"

    from backend.browser.ssrf_guard import is_url_ssrf_safe

    if not is_url_ssrf_safe(url):
        logger.warning("fetch_url refused SSRF-unsafe URL", extra={"url": url})
        return (
            "Error: URL refused — resolves to a private/loopback/link-local/"
            "metadata IP address (blocked for security, SSRF)."
        )

    if not settings.tavily_api_key:
        logger.warning("TAVILY_API_KEY not configured — usando fallback via Chromium")
        return await _fetch_via_fallback(url)

    logger.info("fetch_url tool called", extra={"url": url})

    t0 = time.perf_counter()
    try:
        from backend.persistence.tracer import tracer as _tracer
    except Exception:
        _tracer = None  # type: ignore[assignment]

    try:
        client = _get_extract_tool()
        try:
            results = await _invoke_backend(client, {"url": url})
        except AttributeError:
            logger.warning(
                "backend de busca não suporta extração; usando HTTP direto",
                extra={
                    "url": url,
                    "backend": getattr(client, "name", type(client).__name__),
                },
            )
            return await _fetch_via_http(url, ctx)
        if not results:
            logger.warning("fetch_url returned no content", extra={"url": url})
            if _tracer:
                _tracer.record_sync(
                    "fetch_url",
                    "call",
                    time.perf_counter() - t0,
                    {"url": url[:120], "content_length": 0},
                )
            return f"No content found at {url}"

        content = results[0].get("raw_content", "") or results[0].get("content", "")

        logger.info(
            "fetch_url completed",
            extra={"url": url, "content_length": len(content)},
        )
        if _tracer:
            _tracer.record_sync(
                "fetch_url",
                "call",
                time.perf_counter() - t0,
                {"url": url[:120], "content_length": len(content)},
            )
        from backend.services.prompt_injection import (
            detect_injection,
            envelope_untrusted,
        )

        if (pattern := detect_injection(content)) is not None:
            logger.warning(
                "fetch_url: padrão de prompt injection detectado (log-only)",
                extra={"url": url, "pattern": pattern},
            )
        return envelope_untrusted(content, source=url)

    except Exception as e:
        err = str(e)
        logger.exception("fetch_url failed", extra={"url": url})
        if _tracer:
            _tracer.record_sync(
                "fetch_url",
                "call",
                time.perf_counter() - t0,
                {"url": url[:120]},
                status="error",
            )
        if _is_quota_error(err):
            logger.warning(
                "Tavily quota/rate limit atingido — usando fallback via Chromium",
                extra={"url": url},
            )
            return await _fetch_via_fallback(url)
        if "no extracted results" in err.lower():
            return f"No content found at {url}"
        return (
            "Error occurred fetching URL. The configured extraction backend "
            "failed; check its configuration or retry."
        )


@vtool(
    extras=ToolExtras(
        render_hint="search_results",
        category="web",
        destructive=True,
        icon="globe",
    )
)
async def web_crawl(
    url: str,
    max_depth: int = 1,
    limit: int = 20,
    instructions: str | None = None,
) -> str:
    """Varre um site a partir de uma URL, seguindo links internos.

    Custa créditos por página e gera carga no site alvo — por isso exige
    aprovação antes de rodar. Para uma página só, use `fetch_url`.

    Args:
        url: URL raiz da varredura.
        max_depth: profundidade máxima (1 a 5).
        limit: teto de páginas processadas.
        instructions: orientação em linguagem natural do que procurar.

    Returns:
        JSON com as páginas varridas, ou objeto com `error`.
    """
    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": "URL deve começar com http:// ou https://"})
    if not settings.tavily_api_key:
        return json.dumps(
            {"error": "web_crawl exige TAVILY_API_KEY — sem fallback para varredura"}
        )

    try:
        client = _tavily_client()
        saida = await client.crawl(
            url,
            max_depth=max_depth,
            limit=limit,
            instructions=instructions,
        )
        return json.dumps(saida.get("results", []))
    except Exception as exc:
        logger.exception("web_crawl failed", extra={"url": url})
        return json.dumps({"error": str(exc)})


@vtool(
    extras=ToolExtras(
        render_hint="search_results",
        category="web",
        destructive=True,
        icon="globe",
    )
)
async def web_map(url: str, limit: int = 50) -> str:
    """Mapeia a estrutura de links de um site, sem extrair o conteúdo.

    Mais barato que `web_crawl` (não lê as páginas), mas ainda percorre o
    site — exige aprovação pelo mesmo motivo.

    Args:
        url: URL raiz do mapeamento.
        limit: teto de links retornados.

    Returns:
        JSON com a lista de URLs encontradas, ou objeto com `error`.
    """
    if not url.startswith(("http://", "https://")):
        return json.dumps({"error": "URL deve começar com http:// ou https://"})
    if not settings.tavily_api_key:
        return json.dumps(
            {"error": "web_map exige TAVILY_API_KEY — sem fallback para mapeamento"}
        )

    try:
        client = _tavily_client()
        saida = await client.map(url, limit=limit)
        return json.dumps(saida.get("results", []))
    except Exception as exc:
        logger.exception("web_map failed", extra={"url": url})
        return json.dumps({"error": str(exc)})
