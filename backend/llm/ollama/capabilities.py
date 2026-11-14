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
from dataclasses import dataclass

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
