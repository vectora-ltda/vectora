"""Deep research do Tavily — ``POST /research``, assíncrono.

Responde **201** com ``request_id``/``status``; o resultado vem por polling
(a API também oferece SSE com ``stream: true``, não usado aqui).

O teto de tempo é obrigatório: um loop de espera sem corte gira para sempre
quando o outro lado nunca conclui, e o sintoma chega como travamento, não
como erro.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_TERMINAIS_DE_FALHA = frozenset({"failed", "cancelled", "expired", "error"})

_DEFAULT_POLL_INTERVAL_S = 5.0
#: Deep research leva minutos; o teto é generoso mas existe.
_DEFAULT_TIMEOUT_S = 600.0
