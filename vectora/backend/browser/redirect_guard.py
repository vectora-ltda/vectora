"""Bloqueio de egress por salto de redirect no Chromium controlado via CDP.

``page.route`` do Playwright só é consultado para a requisição inicial: o
navegador segue os redirects seguintes sem chamar o handler, então um destino
privado atrás de um host permitido seria alcançado antes de qualquer checagem.
O domínio ``Fetch`` do CDP pausa cada requisição, inclusive as geradas por
redirects, antes de o navegador enviá-la; esta guarda valida a URL de cada
salto e falha a requisição bloqueada com ``BlockedByClient``.

O navegador resolve o DNS por conta própria depois da validação, portanto o
guard não elimina DNS rebinding.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

#: ``(url) -> (permitido, motivo)``.
UrlCheck = Callable[[str], Awaitable[tuple[bool, str]]]

_CHECKED_SCHEMES = frozenset({"http", "https"})


async def install_redirect_guard(
    page: Any, check_url: UrlCheck, blocked: dict[str, str]
) -> Callable[[], Awaitable[None]] | None:
    """Pausa toda requisição http(s) da página e bloqueia as não permitidas.

    Devolve a função que desfaz a guarda, ou ``None`` quando a página não
    expõe uma sessão CDP (adaptadores de teste), caso em que a validação do
    URL final após ``goto`` continua sendo a única checagem de redirect.
    ``blocked["reason"]`` recebe o motivo da última requisição recusada.
    """
    context = getattr(page, "context", None)
    new_session: Any = getattr(context, "new_cdp_session", None)
    if not callable(new_session):
        return None
    session = await new_session(page)
    pending: set[asyncio.Task[None]] = set()

    async def _resolve(event: dict[str, Any]) -> None:
        request_id = event["requestId"]
        url = str(event.get("request", {}).get("url", ""))
        allowed = True
        reason = ""
        if urlparse(url).scheme.lower() in _CHECKED_SCHEMES:
            try:
                allowed, reason = await check_url(url)
            except Exception:
                logger.exception("redirect_guard: falha ao validar a URL")
                allowed, reason = False, "falha ao validar a URL"
        try:
            if allowed:
                await session.send("Fetch.continueRequest", {"requestId": request_id})
            else:
                blocked["reason"] = reason
                await session.send(
                    "Fetch.failRequest",
                    {"requestId": request_id, "errorReason": "BlockedByClient"},
                )
        except Exception:
            logger.debug("redirect_guard: requisição já encerrada", exc_info=True)

    def _on_paused(event: dict[str, Any]) -> None:
        task = asyncio.ensure_future(_resolve(event))
        pending.add(task)
        task.add_done_callback(pending.discard)

    session.on("Fetch.requestPaused", _on_paused)
    await session.send("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})

    async def release() -> None:
        with contextlib.suppress(Exception):
            await session.send("Fetch.disable")
        with contextlib.suppress(Exception):
            await session.detach()
        for task in tuple(pending):
            task.cancel()

    return release
