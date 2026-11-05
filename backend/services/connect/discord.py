"""Adapter do Discord — WebSocket Gateway via `discord.py`.

Conexão outbound persistente, sem endpoint HTTP público. O usuário cria a
própria Application no Discord Developer Portal, habilita o intent de
conteúdo de mensagem e convida o bot pro servidor dele.
"""

from __future__ import annotations

import importlib.util
import logging
from typing import Any

from backend.services.connect.runner import process_incoming
from backend.services.gateway.messaging import IncomingMessage

logger = logging.getLogger(__name__)

PLATFORM = "discord"

#: Import dentro das funções — `discord.py` abre um pool aiohttp no import e
#: o backend sobe sem tocar em Discord na maioria das instalações.
DISCORD_AVAILABLE = importlib.util.find_spec("discord") is not None


def to_incoming(message: Any, *, bot_user_id: Any = None) -> IncomingMessage | None:
    """Traduz um `discord.Message` pro formato comum.

    `None` quando é mensagem do próprio bot (senão ele responderia a si mesmo
    num laço infinito) ou quando não há texto — anexo puro não é turno de
    conversa.
    """
    author = getattr(message, "author", None)
    author_id = getattr(author, "id", None)
    if getattr(author, "bot", False) or (
        bot_user_id is not None and author_id == bot_user_id
    ):
        return None
    text = getattr(message, "content", None)
    if not text or not str(text).strip():
        return None
    channel_id = getattr(getattr(message, "channel", None), "id", None)
    if channel_id is None:
        return None
    return IncomingMessage(
        platform=PLATFORM,
        platform_user_id=str(channel_id),
        text=str(text),
    )


async def handle_message(client: Any, message: Any) -> None:
    """Defensivo: exceção aqui mataria o handler do `discord.py` e a conexão
    ficaria viva mas surda."""
    try:
        bot_user_id = getattr(getattr(client, "user", None), "id", None)
        incoming = to_incoming(message, bot_user_id=bot_user_id)
        if incoming is None:
            return
        outgoing = await process_incoming(incoming)
        await message.channel.send(outgoing.text)
    except Exception:
        logger.exception("connect.discord: falha ao tratar mensagem")


def build_client() -> Any:
    """Client com o intent de conteúdo de mensagem ligado — sem ele o
    `message.content` chega vazio e o bot parece mudo sem erro nenhum."""
    if not DISCORD_AVAILABLE:
        msg = "pacote 'discord.py' não instalado — integração Discord indisponível"
        raise RuntimeError(msg)
    import discord

    intents = discord.Intents.default()
    intents.message_content = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_message(message: Any) -> None:  # pragma: no cover - callback
        await handle_message(client, message)

    return client


async def start(token: str) -> Any:
    """Conecta o client como task asyncio e devolve para o manager parar."""
    import asyncio

    client = build_client()
    task = asyncio.create_task(client.start(token))
    client._vectora_task = task
    logger.info("connect.discord: cliente iniciado")
    return client


async def stop(client: Any) -> None:
    """Parada idempotente — client já fechado não levanta."""
    if client is None:
        return
    try:
        await client.close()
        task = getattr(client, "_vectora_task", None)
        if task is not None:
            task.cancel()
    except Exception:
        logger.exception("connect.discord: falha ao parar cliente")
