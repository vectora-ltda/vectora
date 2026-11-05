"""Capacidades por modelo via ``POST /api/show``.

Fonte de verdade do que cada modelo local faz: o array ``capabilities``
(`vision`, `thinking`, `tools`, `embedding`) e o `context_length`. Antes disso
o Vectora resolvia capacidade por configuração ("o usuário escolheu um modelo
de imagem?"), que é palpite, não detecção.

Invariante: **falha fechada**. Servidor fora do ar devolve nenhuma capacidade,
nunca todas — falhar aberto faz a UI oferecer o que vai quebrar depois.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from backend.llm.ollama.client import OllamaClient, OllamaError

logger = logging.getLogger(__name__)

#: O resultado só muda quando o usuário baixa/remove modelo — TTL generoso.
_CACHE_TTL_S = 600.0

_cache: dict[str, tuple[float, ModelCapabilities]] = {}


@dataclass(frozen=True)
class ModelCapabilities:
    vision: bool = False
    thinking: bool = False
    tools: bool = False
    embedding: bool = False
    context_length: int | None = None


def clear_capabilities_cache() -> None:
    _cache.clear()


def _do_model_info(info: dict) -> tuple[bool, int | None]:
    """Fallback pra servidor anterior ao campo `capabilities`.

    Mesmo caminho do Hermes: a presença de `*.vision.block_count` denuncia um
    modelo multimodal.
    """
    tem_visao = any(".vision." in str(k) for k in info)
    contexto: int | None = None
    for chave, valor in info.items():
        if str(chave).endswith(".context_length"):
            try:
                contexto = int(valor)
            except (TypeError, ValueError):
                contexto = None
            break
    return tem_visao, contexto


async def fetch_model_capabilities(
    client: OllamaClient, model: str
) -> ModelCapabilities:
    """Consulta `/api/show` (com cache) e devolve as capacidades do modelo."""
    chave = f"{client.base_url}::{model}"
    agora = time.monotonic()
    em_cache = _cache.get(chave)
    if em_cache and agora - em_cache[0] < _CACHE_TTL_S:
        return em_cache[1]

    try:
        corpo = await client.post_json("/api/show", {"model": model})
    except (OllamaError, Exception):
        # Falha fechada: sem resposta, nenhuma capacidade é assumida.
        logger.info(
            "ollama: /api/show indisponível — nenhuma capacidade assumida",
            extra={"model": model},
            exc_info=True,
        )
        return ModelCapabilities()

    brutas = corpo.get("capabilities")
    info = corpo.get("model_info") or {}
    visao_fallback, contexto_fallback = _do_model_info(info)

    if isinstance(brutas, list):
        nomes = {str(c).lower() for c in brutas}
        caps = ModelCapabilities(
            vision="vision" in nomes or visao_fallback,
            thinking="thinking" in nomes,
            tools="tools" in nomes,
            embedding="embedding" in nomes,
            context_length=contexto_fallback,
        )
    else:
        caps = ModelCapabilities(
            vision=visao_fallback, context_length=contexto_fallback
        )

    _cache[chave] = (agora, caps)
    return caps
