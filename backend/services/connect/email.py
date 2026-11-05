"""Adapter de email — polling IMAP + envio SMTP direto (stdlib).

Sem provider transacional e sem webhook: o Vectora se conecta à caixa do
próprio usuário (IMAP para ler, SMTP para responder), com a senha de app do
provedor dele. Isso mantém a integração inteiramente outbound, igual às outras
três plataformas.

`imaplib`/`smtplib` são síncronos e bloqueantes — toda chamada real vai por
`asyncio.to_thread` para não travar o event loop (CLAUDE.md regra 10).
"""

from __future__ import annotations

import asyncio
import email as email_lib
import email.utils
import imaplib
import logging
import smtplib
from dataclasses import dataclass
from email.header import decode_header, make_header
from email.message import EmailMessage
from typing import Any

from backend.services.connect.runner import process_incoming
from backend.services.gateway.messaging import IncomingMessage

logger = logging.getLogger(__name__)

PLATFORM = "email"

DEFAULT_POLL_INTERVAL_S = 30.0

#: Teto de caracteres do corpo passado ao agente. Threads longas acumulam todo
#: o histórico citado abaixo da resposta; sem corte, cada turno reenviaria a
#: conversa inteira ao LLM.
MAX_BODY_CHARS = 4000


@dataclass(frozen=True)
class EmailConfig:
    imap_host: str
    imap_user: str
    imap_password: str
    smtp_host: str
    smtp_port: int = 587
    imap_port: int = 993
    mailbox: str = "INBOX"


def _decode(value: str | None) -> str:
    """Cabeçalho MIME-encoded (`=?utf-8?B?...?=`) vira texto legível; valor
    malformado degrada pro bruto em vez de estourar."""
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _plain_body(message: Any) -> str:
    """Primeira parte `text/plain`. Email só-HTML devolve string vazia — é
    melhor não responder do que mandar markup cru pro LLM."""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    charset = part.get_content_charset() or "utf-8"
                    return payload.decode(charset, errors="replace")
        return ""
    payload = message.get_payload(decode=True)
    if isinstance(payload, bytes):
        charset = message.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")
    return str(payload or "")


def to_incoming(raw: bytes) -> IncomingMessage | None:
    """Traduz um email bruto (RFC 822) pro formato comum.

    `None` quando não dá pra identificar o remetente (não haveria pra onde
    responder) ou quando não há corpo em texto.
    """
    try:
        message = email_lib.message_from_bytes(raw)
    except Exception:
        logger.exception("connect.email: mensagem malformada ignorada")
        return None

    sender = email_lib.utils.parseaddr(_decode(message.get("From")))[1]
    if not sender:
        return None

    body = _plain_body(message).strip()
    subject = _decode(message.get("Subject")).strip()
    if not body and not subject:
        return None

    text = f"{subject}\n\n{body}".strip() if subject else body
    return IncomingMessage(
        platform=PLATFORM,
        platform_user_id=sender,
        text=text[:MAX_BODY_CHARS],
    )


def fetch_unseen(config: EmailConfig) -> list[bytes]:
    """Lê e marca como vista cada mensagem nova. Síncrono por natureza
    (`imaplib`) — chamado sempre via `asyncio.to_thread`."""
    messages: list[bytes] = []
    conn = imaplib.IMAP4_SSL(config.imap_host, config.imap_port)
    try:
        conn.login(config.imap_user, config.imap_password)
        conn.select(config.mailbox)
        _status, data = conn.search(None, "UNSEEN")
        for num in (data[0] or b"").split():
            _status, payload = conn.fetch(num.decode("ascii"), "(RFC822)")
            messages.extend(
                part[1]
                for part in payload or []
                if isinstance(part, tuple) and isinstance(part[1], bytes)
            )
    finally:
        with_logout = getattr(conn, "logout", None)
        if with_logout is not None:
            try:
                conn.logout()
            except Exception:
                logger.debug("connect.email: logout IMAP falhou", exc_info=True)
    return messages


def send_reply(config: EmailConfig, to_address: str, text: str) -> None:
    """Resposta via SMTP com STARTTLS. Síncrono — sempre via `to_thread`."""
    message = EmailMessage()
    message["From"] = config.imap_user
    message["To"] = to_address
    message["Subject"] = "Vectora"
    message.set_content(text)

    with smtplib.SMTP(config.smtp_host, config.smtp_port) as smtp:
        smtp.starttls()
        smtp.login(config.imap_user, config.imap_password)
        smtp.send_message(message)


async def poll_once(config: EmailConfig) -> int:
    """Um ciclo de polling: lê as novas, roda o agente e responde cada uma.

    Devolve quantas mensagens foram respondidas. Falha em uma mensagem não
    interrompe as outras nem o loop — uma caixa com um email problemático
    travaria a integração inteira.
    """
    try:
        raws = await asyncio.to_thread(fetch_unseen, config)
    except Exception:
        logger.exception("connect.email: falha ao ler a caixa de entrada")
        return 0

    respondidas = 0
    for raw in raws:
        incoming = to_incoming(raw)
        if incoming is None:
            continue
        try:
            outgoing = await process_incoming(incoming)
            await asyncio.to_thread(
                send_reply, config, incoming.platform_user_id, outgoing.text
            )
            respondidas += 1
        except Exception:
            logger.exception(
                "connect.email: falha ao responder %s", incoming.platform_user_id
            )
    return respondidas


async def poll_loop(
    config: EmailConfig, *, interval_s: float = DEFAULT_POLL_INTERVAL_S
) -> None:
    """Loop até cancelamento — o manager guarda a task e a cancela no stop."""
    while True:
        await poll_once(config)
        await asyncio.sleep(interval_s)


async def start(
    config: EmailConfig, *, interval_s: float = DEFAULT_POLL_INTERVAL_S
) -> Any:
    task = asyncio.create_task(poll_loop(config, interval_s=interval_s))
    logger.info("connect.email: polling IMAP iniciado (%ss)", interval_s)
    return task


async def stop(task: Any) -> None:
    """Parada idempotente — task já cancelada não levanta."""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        logger.debug("connect.email: polling encerrado", exc_info=True)
