"""Registry de backends de busca web.

Antes deste módulo a escolha era binária e implícita: com `TAVILY_API_KEY`,
Tavily; sem, o fallback DuckDuckGo/Chromium do Electron. Não havia como **escolher**
pesquisar pelo browser embutido — ele só aparecia por falta de chave.

Estrutura copiada do Hermes (`agent/web_search_registry.py:122` mantém uma
ordem de preferência resolvida por disponibilidade), com os backends que o
Vectora tem.

Invariante: escolha explícita do usuário **nunca** é sobrescrita pela ordem
de preferência. Se o backend escolhido está indisponível, o erro diz isso —
cair em outro em silêncio esconderia a credencial faltando.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


class SearchBackendUnavailableError(RuntimeError):
    """O backend pedido não existe ou não tem credencial configurada."""


@dataclass(frozen=True)
class SearchBackend:
    name: str
    label: str
    #: Sem credencial configurada o backend não entra na resolução automática
    #: e é recusado na escolha explícita.
    is_available: Callable[[], bool]
    search: Callable[..., Awaitable[list[dict]]]
    #: True quando não exige credencial nenhuma.
    keyless: bool = False


async def _search_tavily(query: str, max_results: int = 5) -> list[dict]:
    from backend.settings import settings
    from backend.tools.tavily.client import TavilyClient

    client = TavilyClient(api_key=settings.tavily_api_key or "")
    try:
        return await client.search(
            query, max_results=max_results, search_depth="advanced"
        )
    finally:
        await client.aclose()


async def _search_ollama_web(query: str, max_results: int = 5) -> list[dict]:
    from backend.llm.ollama.web_search import ollama_web_search
    from backend.settings import settings

    return await ollama_web_search(
        query,
        api_key=settings.ollama_api_key or "",
        max_results=min(max_results, 10),
    )


async def _search_browser(query: str, max_results: int = 5) -> list[dict]:
    """Busca no Chromium do Electron, reaproveitando os logins do usuário."""
    from backend.browser.search_fallback import search_fallback

    return search_fallback(query, max_results=max_results)


async def _search_duckduckgo(query: str, max_results: int = 5) -> list[dict]:
    from backend.browser.search_fallback import search_fallback

    return search_fallback(query, max_results=max_results)


def _tem_tavily() -> bool:
    from backend.settings import settings

    return bool((settings.tavily_api_key or "").strip())


def _tem_ollama_cloud() -> bool:
    from backend.llm.ollama.web_search import web_search_available

    return web_search_available()


#: Ordem de preferência quando o usuário não escolheu. Backends com
#: credencial vêm primeiro (quem configurou uma chave espera usá-la), e entre
#: os sem credencial o DuckDuckGo vem antes do browser: é o default histórico
#: e não conecta ao Electron só pra buscar. O browser é escolha explícita — é o
#: caso em que os logins da sessão importam.
_BACKENDS: tuple[SearchBackend, ...] = (
    SearchBackend(
        name="tavily",
        label="Tavily",
        is_available=_tem_tavily,
        search=_search_tavily,
    ),
    SearchBackend(
        name="ollama-web",
        label="Ollama Cloud",
        is_available=_tem_ollama_cloud,
        search=_search_ollama_web,
    ),
    SearchBackend(
        name="duckduckgo",
        label="DuckDuckGo",
        is_available=lambda: True,
        search=_search_duckduckgo,
        keyless=True,
    ),
    SearchBackend(
        name="browser",
        label="Browser embutido",
        is_available=lambda: True,
        search=_search_browser,
        keyless=True,
    ),
)

_POR_NOME = {b.name: b for b in _BACKENDS}


def available_backends() -> list[SearchBackend]:
    """Backends utilizáveis agora — usado pelo seletor das Settings."""
    return [b for b in _BACKENDS if b.is_available()]


def _escolha_do_usuario() -> str:
    try:
        from backend.workspace.runtime_settings import runtime_settings

        return str(
            runtime_settings.rag_settings.get("search_backend", "") or ""
        ).strip()
    except Exception:
        return ""


def resolve_backend() -> SearchBackend:
    """Backend a usar: escolha explícita do usuário, ou a ordem de preferência.

    Escolha explícita indisponível levanta em vez de cair em outro backend —
    o usuário pediu aquele, e precisa saber que a credencial falta.
    """
    escolhido = _escolha_do_usuario()
    if escolhido and escolhido != "auto":
        backend = _POR_NOME.get(escolhido)
        if backend is None:
            disponiveis = ", ".join(_POR_NOME)
            msg = (
                f"backend de busca {escolhido!r} não existe — "
                f"opções: {disponiveis}, auto"
            )
            raise SearchBackendUnavailableError(msg)
        if not backend.is_available():
            msg = (
                f"backend de busca {escolhido!r} está selecionado mas não tem "
                "credencial configurada — configure a chave em Integrações ou "
                "escolha outro backend"
            )
            raise SearchBackendUnavailableError(msg)
        return backend

    for backend in _BACKENDS:
        if backend.is_available():
            return backend

    # Inalcançável enquanto browser/duckduckgo forem keyless, mas o erro
    # explícito é melhor que devolver lista vazia, que o LLM leria como
    # "não há resultados" em vez de "não há busca".
    msg = "nenhum backend de busca disponível"
    raise SearchBackendUnavailableError(msg)


def backend_choices() -> list[dict[str, Any]]:
    """Opções pro seletor das Settings, com disponibilidade por item."""
    return [
        {
            "value": b.name,
            "label": b.label,
            "available": b.is_available(),
            "keyless": b.keyless,
        }
        for b in _BACKENDS
    ]
