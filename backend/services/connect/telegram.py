"""Adapter do Telegram — long polling via `python-telegram-bot`.

Long polling (não webhook): o processo abre a conexão de saída pro
api.telegram.org, então funciona atrás de NAT, sem domínio nem TLS. O usuário
cria o próprio bot com `/newbot` no BotFather e cola o token nas Settings.
"""

from __future__ import annotations

import importlib.util
import logging
from typing import Any

from backend.services.connect.runner import process_incoming
from backend.services.gateway.messaging import IncomingMessage

logger = logging.getLogger(__name__)

PLATFORM = "telegram"

#: A lib entra como dependência normal, mas o import fica dentro das funções:
#: `python-telegram-bot` puxa httpx/apscheduler no import e o backend sobe sem
#: nunca tocar em Telegram na maioria das instalações.
TELEGRAM_AVAILABLE = importlib.util.find_spec("telegram") is not None


def to_incoming(update: Any) -> IncomingMessage | None:
    """Traduz um `telegram.Update` pro formato comum.

    `None` para update sem texto (sticker, foto sem legenda, entrada/saída de
    membro): a plataforma manda muitos updates que não são turno de conversa,
    e tratá-los como mensagem vazia acordaria o agente à toa.
    """
    message = getattr(update, "message", None)
    if message is None:
        return None
    text = getattr(message, "text", None) or getattr(message, "caption", None)
    if not text or not str(text).strip():
        return None
    chat = getattr(message, "chat", None)
    chat_id = getattr(chat, "id", None)
    if chat_id is None:
        return None
    return IncomingMessage(
        platform=PLATFORM,
        platform_user_id=str(chat_id),
        text=str(text),
    )


async def handle_update(update: Any, context: Any = None) -> None:
    """Handler registrado no `Application`. Defensivo: exceção aqui derrubaria
    o loop de polling e a integração ficaria muda até o próximo boot."""
    del context
    try:
        incoming = to_incoming(update)
        if incoming is None:
            return
        outgoing = await process_incoming(incoming)
        await update.message.reply_text(outgoing.text)
    except Exception:
        logger.exception("connect.telegram: falha ao tratar update")


def build_application(token: str) -> Any:
    """Monta o `Application` com o handler de texto registrado."""
    if not TELEGRAM_AVAILABLE:
        msg = (
            "pacote 'python-telegram-bot' não instalado — integração Telegram "
            "indisponível"
        )
        raise RuntimeError(msg)
    from telegram.ext import Application, MessageHandler, filters

    app = Application.builder().token(token).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_update))
    return app


async def start(token: str) -> Any:
    """Inicia o polling como task asyncio no processo do backend (não
    subprocess) e devolve o `Application` para o manager parar depois."""
    app = build_application(token)
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    logger.info("connect.telegram: polling iniciado")
    return app


async def stop(app: Any) -> None:
    """Parada idempotente — chamar de novo num app já parado não levanta."""
    if app is None:
        return
    try:
        if getattr(app, "updater", None) is not None:
            await app.updater.stop()
        await app.stop()
        await app.shutdown()
    except Exception:
        logger.exception("connect.telegram: falha ao parar polling")
