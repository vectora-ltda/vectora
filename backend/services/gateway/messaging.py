"""Vectora Connect — camada de abstração de mensageria multi-plataforma.

Cada plataforma externa (Telegram, Discord, Slack, WhatsApp, Signal, Email)
normaliza sua mensagem nativa pro formato comum aqui definido antes de
entrar no motor de chat já existente — a plataforma é só mais uma origem
de turno de conversa, não um caminho de código paralelo.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

#: Resolve um platform_user_id já mapeado pra um thread_id existente, ou
#: None se essa é a primeira mensagem dessa conversa externa.
ThreadLookup = Callable[[str, str], Awaitable[str | None]]

#: Cria um thread novo + grava o mapeamento (platform, platform_user_id) ->
#: thread_id; devolve o thread_id criado.
ThreadCreator = Callable[[str, str], Awaitable[str]]


@dataclass(frozen=True)
class IncomingMessage:
    """Mensagem normalizada vinda de uma plataforma externa."""

    platform: str
    platform_user_id: str
    text: str
    attachments: tuple[str, ...] = ()


@dataclass(frozen=True)
class OutgoingMessage:
    """Resposta do agente a devolver pra plataforma externa."""

    platform: str
    platform_user_id: str
    text: str


async def resolve_thread_id(
    incoming: IncomingMessage,
    *,
    lookup: ThreadLookup,
    create: ThreadCreator,
) -> str:
    """Resolve o `thread_id` do Vectora pra uma mensagem externa —
    reaproveita a conversa já existente pra esse `platform_user_id`, ou cria
    uma nova na primeira mensagem. `lookup`/`create` são injetados (não
    fixos numa storage específica) — cada integração decide onde persistir
    o mapeamento.
    """
    existing = await lookup(incoming.platform, incoming.platform_user_id)
    if existing is not None:
        return existing
    thread_id = await create(incoming.platform, incoming.platform_user_id)
    logger.info(
        "gateway.messaging: novo thread %s criado para %s:%s",
        thread_id,
        incoming.platform,
        incoming.platform_user_id,
    )
    return thread_id


async def handle_incoming_message(
    incoming: IncomingMessage,
    *,
    lookup: ThreadLookup,
    create: ThreadCreator,
    run_agent: Callable[[str, str], Awaitable[str]],
) -> OutgoingMessage:
    """Fecha o loop completo: resolve o thread, roda o agente com o texto
    da mensagem, devolve a resposta pronta pra plataforma enviar de volta.

    Erro/borda: falha ao processar (agente indisponível, etc.) nunca deixa
    o usuário externo sem resposta nenhuma — devolve uma mensagem de erro
    amigável em vez de propagar a exceção (tools defensivas, CLAUDE.md
    regra 11 — mesmo princípio aplicado aqui na borda de mensageria).
    """
    try:
        thread_id = await resolve_thread_id(incoming, lookup=lookup, create=create)
        reply_text = await run_agent(thread_id, incoming.text)
    except Exception:
        logger.exception(
            "gateway.messaging: falha ao processar mensagem de %s:%s",
            incoming.platform,
            incoming.platform_user_id,
        )
        reply_text = (
            "Desculpe, não consegui processar sua mensagem agora. Tente novamente."
        )
    return OutgoingMessage(
        platform=incoming.platform,
        platform_user_id=incoming.platform_user_id,
        text=reply_text,
    )
