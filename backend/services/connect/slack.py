"""Adapter do Slack — Socket Mode via `slack_bolt`.

Socket Mode, não Events API: a conexão WebSocket sai do processo pro Slack, o
que dispensa endpoint HTTP público, domínio e verificação de assinatura. O
usuário cria o próprio Slack App, habilita Socket Mode e gera os dois tokens
(bot `xoxb-` e app-level `xapp-`).
"""

from __future__ import annotations

import importlib.util
import logging
from typing import Any

from backend.services.connect.runner import process_incoming
from backend.services.gateway.messaging import IncomingMessage

logger = logging.getLogger(__name__)

PLATFORM = "slack"

#: Import dentro das funções — `slack_bolt` carrega o SDK inteiro no import.
SLACK_AVAILABLE = importlib.util.find_spec("slack_bolt") is not None


def to_incoming(event: dict[str, Any]) -> IncomingMessage | None:
    """Traduz um evento `message` do Socket Mode pro formato comum.

    `None` para eventos com `bot_id` (resposta do próprio app — evita laço),
    subtipos de edição/remoção (`message_changed`, `message_deleted`) e
    mensagens sem texto.
    """
    if not isinstance(event, dict):
        return None
    if event.get("bot_id"):
        return None
    if event.get("subtype"):
        return None
    text = event.get("text")
    if not text or not str(text).strip():
        return None
    channel = event.get("channel")
    if not channel:
        return None
    return IncomingMessage(
        platform=PLATFORM,
        platform_user_id=str(channel),
        text=str(text),
    )


async def handle_event(event: dict[str, Any], say: Any) -> None:
    """Defensivo: exceção propagada faria o bolt marcar o evento como não
    processado e o Slack reentregaria o mesmo evento em loop."""
    try:
        incoming = to_incoming(event)
        if incoming is None:
            return
        outgoing = await process_incoming(incoming)
        await say(outgoing.text)
    except Exception:
        logger.exception("connect.slack: falha ao tratar evento")


def build_app(bot_token: str) -> Any:
    if not SLACK_AVAILABLE:
        msg = "pacote 'slack_bolt' não instalado — integração Slack indisponível"
        raise RuntimeError(msg)
    from slack_bolt.async_app import AsyncApp

    app = AsyncApp(token=bot_token)

    @app.event("message")
    async def _on_message(event: dict[str, Any], say: Any) -> None:  # pragma: no cover
        await handle_event(event, say)

    return app


async def start(bot_token: str, app_token: str) -> Any:
    """Abre a conexão Socket Mode e devolve o handler para o manager parar.

    Os dois tokens são obrigatórios e têm papéis distintos: o `xoxb-`
    autentica as chamadas de API, o `xapp-` abre o WebSocket.
    """
    if not bot_token or not app_token:
        msg = "Slack exige SLACK_BOT_TOKEN (xoxb-) e SLACK_APP_TOKEN (xapp-)"
        raise ValueError(msg)
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    app = build_app(bot_token)
    handler = AsyncSocketModeHandler(app, app_token)
    await handler.connect_async()
    logger.info("connect.slack: socket mode conectado")
    return handler


async def stop(handler: Any) -> None:
    """Parada idempotente."""
    if handler is None:
        return
    try:
        await handler.close_async()
    except Exception:
        logger.exception("connect.slack: falha ao fechar socket mode")
