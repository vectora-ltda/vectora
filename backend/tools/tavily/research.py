"""Deep research do Tavily — ``POST /research``, assíncrono.

Responde **201** com ``request_id``/``status``; o resultado vem por polling
(a API também oferece SSE com ``stream: true``, não usado aqui).

O teto de tempo é obrigatório: um loop de espera sem corte gira para sempre
quando o outro lado nunca conclui, e o sintoma chega como travamento, não
como erro.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from backend.tools.tavily.client import TavilyClient, TavilyResponseError

logger = logging.getLogger(__name__)

_TERMINAIS_DE_FALHA = frozenset({"failed", "cancelled", "expired", "error"})

_DEFAULT_POLL_INTERVAL_S = 5.0
#: Deep research leva minutos; o teto é generoso mas existe.
_DEFAULT_TIMEOUT_S = 600.0


class ResearchTimeoutError(TavilyResponseError):
    """Não concluiu dentro do teto.

    Separado das falhas do provider: o job pode seguir rodando lá, e o
    chamador precisa poder oferecer "consultar de novo".
    """


async def run_research(
    client: TavilyClient,
    input_text: str,
    *,
    model: str | None = None,
    poll_interval_s: float = _DEFAULT_POLL_INTERVAL_S,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
    **kwargs: Any,
) -> dict:
    """Dispara a pesquisa e acompanha até um estado terminal."""
    payload: dict[str, Any] = {"input": input_text, **kwargs}
    if model:
        payload["model"] = model

    inicio = await client._request("POST", "/research", payload)
    request_id = str(inicio.get("request_id") or "")
    if not request_id:
        # Sem id não há como acompanhar: o job rodaria e cobraria sem
        # ninguém buscar o resultado.
        msg = (
            "Tavily respondeu /research sem `request_id` — job impossível de acompanhar"
        )
        raise TavilyResponseError(msg)

    limite = time.monotonic() + timeout_s

    while True:
        estado = await client._request("GET", f"/research/{request_id}")
        status = str(estado.get("status") or "")

        if status == "completed":
            if not estado.get("output"):
                msg = "Tavily marcou a pesquisa como completed mas sem `output`"
                raise TavilyResponseError(msg)
            return estado

        if status in _TERMINAIS_DE_FALHA:
            detalhe = estado.get("error") or "sem detalhe do provider"
            msg = f"pesquisa terminou em `{status}`: {detalhe}"
            raise TavilyResponseError(msg)

        if time.monotonic() >= limite:
            msg = (
                f"pesquisa não concluiu em {timeout_s:.0f}s (último status: "
                f"{status or 'desconhecido'}, request {request_id}) — pode "
                "seguir rodando no Tavily"
            )
            raise ResearchTimeoutError(msg)

        if poll_interval_s:
            await asyncio.sleep(poll_interval_s)
