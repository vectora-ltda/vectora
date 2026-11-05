"""Cliente HTTP do Tavily.

Base ``https://api.tavily.com`` (override por ``TAVILY_BASE_URL``), auth
``Authorization: Bearer tvly-...``.

401 e 429 viram exceções **distintas** de propósito: chave inválida e cota
estourada exigem ações diferentes do usuário, e hoje viram a mesma mensagem.
"""

from __future__ import annotations

import logging
from typing import Any, Self

logger = logging.getLogger(__name__)

BASE_URL = "https://api.tavily.com"
_TIMEOUT_S = 60.0


class TavilyError(RuntimeError):
    """Base de toda falha do Tavily."""


class TavilyAuthError(TavilyError):
    """Key ausente ou rejeitada (401/403)."""


class TavilyQuotaError(TavilyError):
    """Créditos esgotados ou rate limit (429)."""


class TavilyResponseError(TavilyError):
    """Resposta com forma inesperada, ou URL que a extração não conseguiu ler."""


def _sem_none(payload: dict) -> dict:
    """Remove campos não informados.

    Mandar `include_domains: null` restringe a busca em alguns casos em vez
    de deixá-la aberta — o campo tem que sumir, não ir nulo.
    """
    return {k: v for k, v in payload.items() if v is not None}


class TavilyClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = BASE_URL,
        http_client: Any = None,
        timeout_s: float = _TIMEOUT_S,
    ) -> None:
        if not (api_key or "").strip():
            msg = (
                "TAVILY_API_KEY não configurada — configure a chave em "
                "Integrações antes de usar a busca via Tavily."
            )
            raise TavilyAuthError(msg)
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_s = timeout_s
        self._client = http_client

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def _ensure_client(self) -> Any:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=self._timeout_s)
        return self._client

    async def __aenter__(self) -> Self:
        await self._ensure_client()
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        import contextlib

        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.aclose()

    def _raise_for_status(self, status: int, corpo: Any, *, path: str) -> None:
        if status < 400:
            return
        detalhe = ""
        if isinstance(corpo, dict):
            detalhe = str(corpo.get("detail") or corpo.get("error") or "")
        if status in (401, 403):
            msg = f"TAVILY_API_KEY rejeitada pelo Tavily: {detalhe or status}"
            raise TavilyAuthError(msg)
        if status == 429:
            msg = (
                f"Tavily sem créditos ou em rate limit: {detalhe or status} — "
                "confira o consumo no painel do Tavily"
            )
            raise TavilyQuotaError(msg)
        msg = f"Tavily respondeu {status} em {path}: {detalhe or 'sem detalhe'}"
        raise TavilyResponseError(msg)

    async def _request(
        self, method: str, path: str, payload: dict | None = None
    ) -> dict:
        client = await self._ensure_client()
        if method == "GET":
            resp = await client.get(f"{self._base_url}{path}", headers=self._headers())
        else:
            resp = await client.post(
                f"{self._base_url}{path}", json=payload or {}, headers=self._headers()
            )
        try:
            corpo = resp.json()
        except Exception:
            corpo = None
        self._raise_for_status(resp.status_code, corpo, path=path)
        if not isinstance(corpo, dict):
            trecho = (resp.text or "")[:200]
            msg = f"Tavily devolveu corpo não-JSON em {path}: {trecho!r}"
            raise TavilyResponseError(msg)
        return corpo

    async def search(
        self,
        query: str,
        *,
        topic: str | None = None,
        time_range: str | None = None,
        max_results: int | None = None,
        search_depth: str | None = None,
        include_domains: list[str] | None = None,
        exclude_domains: list[str] | None = None,
        include_raw_content: bool | None = None,
    ) -> list[dict]:
        """Busca web. Parâmetros são por chamada, não por instanciação."""
        corpo = await self._request(
            "POST",
            "/search",
            _sem_none(
                {
                    "query": query,
                    "topic": topic,
                    "time_range": time_range,
                    "max_results": max_results,
                    "search_depth": search_depth,
                    "include_domains": include_domains,
                    "exclude_domains": exclude_domains,
                    "include_raw_content": include_raw_content,
                }
            ),
        )
        resultados = corpo.get("results")
        if not isinstance(resultados, list):
            # `results` ausente é resposta malformada, diferente de
            # `results: []` (busca sem resultado) — confundir os dois
            # esconderia uma quebra de contrato da API.
            msg = "Tavily devolveu /search sem o campo `results`"
            raise TavilyResponseError(msg)
        return resultados

    async def extract(
        self,
        urls: list[str],
        *,
        extract_depth: str | None = None,
        output_format: str | None = None,
        include_images: bool | None = None,
    ) -> list[dict]:
        """Extrai o conteúdo das URLs."""
        if not urls:
            return []

        corpo = await self._request(
            "POST",
            "/extract",
            _sem_none(
                {
                    "urls": urls,
                    "extract_depth": extract_depth,
                    "format": output_format,
                    "include_images": include_images,
                }
            ),
        )
        falhas = corpo.get("failed_results") or []
        resultados = corpo.get("results") or []
        if falhas and not resultados:
            # Ignorar `failed_results` faz o agente achar que a página estava
            # vazia, em vez de saber que não foi lida.
            urls_falhas = ", ".join(
                str(f.get("url") or "?") for f in falhas if isinstance(f, dict)
            )
            msg = f"Tavily não conseguiu extrair: {urls_falhas}"
            raise TavilyResponseError(msg)
        return list(resultados)

    async def crawl(self, url: str, **kwargs: Any) -> dict:
        """Varre um site a partir de `url`."""
        return await self._request("POST", "/crawl", _sem_none({"url": url, **kwargs}))

    async def map(self, url: str, **kwargs: Any) -> dict:
        """Mapeia a estrutura de links de um site."""
        return await self._request("POST", "/map", _sem_none({"url": url, **kwargs}))

    async def usage(self) -> dict:
        """Consumo de créditos da key e do plano — base do medidor (15.19)."""
        return await self._request("GET", "/usage")
