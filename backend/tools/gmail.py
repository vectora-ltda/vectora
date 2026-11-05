"""Tools de Gmail para o agente.

Requer GOOGLE_ACCESS_TOKEN com escopo gmail.readonly.
"""

from __future__ import annotations

import base64
import logging
import os

from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)

_GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"


def _token() -> str:
    tok = os.environ.get("GOOGLE_ACCESS_TOKEN", "")
    if not tok:
        raise RuntimeError(
            "GOOGLE_ACCESS_TOKEN não configurado. Conecte o Google em Integrações."
        )
    return tok


def _decode_body(part: dict) -> str:
    data = part.get("body", {}).get("data", "")
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
    except Exception:
        return ""


def _extract_text(payload: dict) -> str:
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        return _decode_body(payload)
    if mime == "text/html":
        raw = _decode_body(payload)
        # Remove tags HTML de forma mínima
        import re

        return re.sub(r"<[^>]+>", "", raw)
    parts = payload.get("parts", [])
    for part in parts:
        text = _extract_text(part)
        if text:
            return text
    return ""


@vtool(extras=ToolExtras(destructive=False, category="integrations", icon="mail"))
async def gmail_list(query: str = "", max_results: int = 10) -> str:
    """Lista emails do Gmail com filtros opcionais.

    Args:
        query: Filtro GMail (ex: 'from:boss@empresa.com', 'is:unread', 'subject:fatura').
        max_results: Número máximo de emails (padrão 10, máx 50).
    """
    try:
        import httpx

        token = _token()
        params: dict = {
            "maxResults": min(max_results, 50),
            "labelIds": "INBOX",
        }
        if query:
            params["q"] = query

        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{_GMAIL_BASE}/messages",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            messages = r.json().get("messages", [])

            if not messages:
                return "Nenhum email encontrado."

            summaries = []
            for msg in messages[:10]:
                mid = msg["id"]
                mr = await client.get(
                    f"{_GMAIL_BASE}/messages/{mid}",
                    params={
                        "format": "metadata",
                        "metadataHeaders": ["From", "Subject", "Date"],
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
                headers = {
                    h["name"]: h["value"]
                    for h in mr.json().get("payload", {}).get("headers", [])
                }
                summaries.append(
                    f"• [{mid}] {headers.get('Date', '')} — {headers.get('From', '')} — {headers.get('Subject', '(sem assunto)')}"
                )

        return "\n".join(summaries)
    except Exception as exc:
        logger.exception("gmail_list error")
        return f"Erro ao listar Gmail: {exc}"


@vtool(extras=ToolExtras(destructive=False, category="integrations", icon="file-text"))
async def gmail_read(message_id: str) -> str:
    """Lê o conteúdo completo de um email do Gmail.

    Args:
        message_id: ID do email (obtido via gmail_list).
    """
    try:
        import httpx

        token = _token()
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{_GMAIL_BASE}/messages/{message_id}",
                params={"format": "full"},
                headers={"Authorization": f"Bearer {token}"},
            )
            r.raise_for_status()
            data = r.json()

        payload = data.get("payload", {})
        headers = {h["name"]: h["value"] for h in payload.get("headers", [])}
        body = _extract_text(payload) or "(corpo vazio)"

        return (
            f"De: {headers.get('From', '')}\n"
            f"Para: {headers.get('To', '')}\n"
            f"Data: {headers.get('Date', '')}\n"
            f"Assunto: {headers.get('Subject', '')}\n"
            f"\n{body[:20_000]}"
        )
    except Exception as exc:
        logger.exception("gmail_read error message_id=%s", message_id)
        return f"Erro ao ler email {message_id}: {exc}"
