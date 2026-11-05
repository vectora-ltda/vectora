"""Integração nativa com o OpenRouter — HTTP direto.

Cliente próprio pra dar caminho de primeira classe a provider routing,
`usage.cost` e reasoning do OpenRouter, além de chat: embeddings, rerank,
imagem, TTS, STT e vídeo.
"""

from backend.llm.openrouter.client import (
    OpenRouterAuthError,
    OpenRouterClient,
    OpenRouterCreditError,
    OpenRouterError,
    OpenRouterRateLimitError,
    OpenRouterResponseError,
    OpenRouterServerError,
)

__all__ = [
    "OpenRouterAuthError",
    "OpenRouterClient",
    "OpenRouterCreditError",
    "OpenRouterError",
    "OpenRouterRateLimitError",
    "OpenRouterResponseError",
    "OpenRouterServerError",
]
