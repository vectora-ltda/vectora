"""Web search e web fetch do Ollama Cloud.

``POST https://ollama.com/api/web_search`` e ``/api/web_fetch``, ambos com
``Authorization: Bearer OLLAMA_API_KEY``.

**São cloud.** Não existem no servidor local — apontar pro ``OLLAMA_BASE_URL``
devolve 404. E exigem key mesmo com o Ollama rodando na máquina, o que precisa
estar explícito na mensagem de erro: sem isso o usuário com o servidor local
ligado não entende a recusa.

Entram como mais um backend do roteador de busca, não como tool própria.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

OLLAMA_CLOUD_URL = "https://ollama.com"

#: Limite documentado do `max_results`.
_MAX_RESULTS_LIMITE = 10
_TIMEOUT_S = 30.0

_SEM_KEY = (
    "web search do Ollama é recurso de nuvem (ollama.com) e exige "
    "OLLAMA_API_KEY — não funciona só com o servidor local rodando. "
    "Gere uma chave em https://ollama.com/settings/keys"
)


def web_search_available() -> bool:
    """True quando há key configurada — a capacidade não existe sem ela."""
    from backend.settings import settings

    return bool((getattr(settings, "ollama_api_key", "") or "").strip())


async def _post(
    path: str, payload: dict, *, api_key: str, http_client: Any = None
) -> dict:
    if not (api_key or "").strip():
        raise ValueError(_SEM_KEY)

    import httpx

    client = http_client or httpx.AsyncClient(timeout=_TIMEOUT_S)
    try:
        resp = await client.post(
            f"{OLLAMA_CLOUD_URL}{path}",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if resp.status_code in (401, 403):
            msg = (
                "OLLAMA_API_KEY rejeitada pelo ollama.com — confira a chave "
                "em https://ollama.com/settings/keys"
            )
            raise RuntimeError(msg)
        if resp.status_code >= 400:
            msg = f"ollama.com respondeu {resp.status_code} em {path}"
            raise RuntimeError(msg)
        corpo = resp.json()
        return corpo if isinstance(corpo, dict) else {}
    finally:
        if http_client is None:
            await client.aclose()


async def ollama_web_search(
    query: str,
    *,
    api_key: str,
    max_results: int = 5,
    http_client: Any = None,
) -> list[dict]:
    """Busca na web via Ollama Cloud. Sem resultado devolve lista vazia."""
    if max_results > _MAX_RESULTS_LIMITE:
        msg = (
            f"max_results={max_results} acima do limite de "
            f"{_MAX_RESULTS_LIMITE} do Ollama web search"
        )
        raise ValueError(msg)

    corpo = await _post(
        "/api/web_search",
        {"query": query, "max_results": max_results},
        api_key=api_key,
        http_client=http_client,
    )
    resultados = corpo.get("results")
    if not isinstance(resultados, list):
        # Busca sem resultado é resposta válida, não falha (CLAUDE.md 11).
        return []
    return [
        {
            "title": str(r.get("title") or ""),
            "url": str(r.get("url") or ""),
            "content": str(r.get("content") or ""),
        }
        for r in resultados
        if isinstance(r, dict)
    ]


async def ollama_web_fetch(url: str, *, api_key: str, http_client: Any = None) -> dict:
    """Extrai o conteúdo de uma URL via Ollama Cloud."""
    if not (url or "").strip():
        msg = "url vazia — nada a buscar"
        raise ValueError(msg)

    corpo = await _post(
        "/api/web_fetch", {"url": url}, api_key=api_key, http_client=http_client
    )
    links = corpo.get("links")
    return {
        "title": str(corpo.get("title") or ""),
        "content": str(corpo.get("content") or ""),
        "links": links if isinstance(links, list) else [],
    }
