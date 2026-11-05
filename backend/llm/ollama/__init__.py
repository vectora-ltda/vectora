"""Integração nativa com o Ollama — HTTP direto.

Cliente próprio pra expor o que o endpoint nativo do Ollama de fato
oferece: `images` por mensagem (vision), `message.thinking` separado do
conteúdo, os contadores de token, e `/api/show` como fonte de verdade das
capacidades por modelo.
"""

from backend.llm.ollama.client import (
    OllamaClient,
    OllamaError,
    OllamaModelNotFoundError,
    OllamaResponseError,
    OllamaUnreachableError,
)

__all__ = [
    "OllamaClient",
    "OllamaError",
    "OllamaModelNotFoundError",
    "OllamaResponseError",
    "OllamaUnreachableError",
]
