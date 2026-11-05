"""Integração nativa com o Tavily — HTTP direto.

A API tem seis endpoints: `/search`, `/extract`, `/crawl`, `/map`,
`/research` e `/usage`.
"""

from backend.tools.tavily.client import (
    TavilyAuthError,
    TavilyClient,
    TavilyError,
    TavilyQuotaError,
    TavilyResponseError,
)

__all__ = [
    "TavilyAuthError",
    "TavilyClient",
    "TavilyError",
    "TavilyQuotaError",
    "TavilyResponseError",
]
