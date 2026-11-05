"""Tools de Slack para o agente.

Requer SLACK_BOT_TOKEN no ambiente, com credenciais do Socket Mode configuradas pelo administrador.
"""

from __future__ import annotations

import logging
import os

from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)

_SLACK_BASE = "https://slack.com/api"


def _token() -> str:
    tok = os.environ.get("SLACK_BOT_TOKEN", "")
    if not tok:
        raise RuntimeError(
            "SLACK_BOT_TOKEN não configurado. Conecte o Slack em Integrações."
        )
    return tok


@vtool(extras=ToolExtras(destructive=True, category="integrations", icon="send"))
async def slack_send(channel: str, message: str) -> str:
    """Envia uma mensagem para um canal do Slack.

    Args:
        channel: Nome ou ID do canal (ex: '#geral' ou 'C01234ABCDE').
        message: Texto da mensagem.
    """
    try:
        import httpx

        token = _token()
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{_SLACK_BASE}/chat.postMessage",
                json={"channel": channel, "text": message},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )
        data = r.json()
        if data.get("ok"):
            return f"Mensagem enviada para {channel}."
        return f"Erro Slack: {data.get('error', 'desconhecido')}"
    except Exception as exc:
        logger.exception("slack_send error channel=%s", channel)
        return f"Erro ao enviar para Slack: {exc}"


@vtool(extras=ToolExtras(destructive=False, category="integrations", icon="hash"))
async def slack_list_channels(limit: int = 20) -> str:
    """Lista canais públicos do Slack.

    Args:
        limit: Número máximo de canais (padrão 20).
    """
    try:
        import httpx

        token = _token()
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{_SLACK_BASE}/conversations.list",
                params={"limit": min(limit, 100), "exclude_archived": True},
                headers={"Authorization": f"Bearer {token}"},
            )
        data = r.json()
        if not data.get("ok"):
            return f"Erro Slack: {data.get('error', 'desconhecido')}"
        channels = data.get("channels", [])
        if not channels:
            return "Nenhum canal encontrado."
        lines = [
            f"#{c['name']} (id={c['id']}, membros={c.get('num_members', '?')})"
            for c in channels
        ]
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("slack_list_channels error")
        return f"Erro ao listar canais: {exc}"


@vtool(
    extras=ToolExtras(destructive=False, category="integrations", icon="message-square")
)
async def slack_read(channel: str, limit: int = 20) -> str:
    """Lê mensagens recentes de um canal do Slack.

    Args:
        channel: ID do canal (ex: 'C01234ABCDE').
        limit: Número de mensagens (padrão 20, máx 100).
    """
    try:
        import httpx

        token = _token()
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{_SLACK_BASE}/conversations.history",
                params={"channel": channel, "limit": min(limit, 100)},
                headers={"Authorization": f"Bearer {token}"},
            )
        data = r.json()
        if not data.get("ok"):
            return f"Erro Slack: {data.get('error', 'desconhecido')}"
        messages = data.get("messages", [])
        if not messages:
            return "Canal sem mensagens recentes."
        lines = []
        for m in reversed(messages):
            user = m.get("user", "app")
            text = m.get("text", "")[:200]
            lines.append(f"[{user}] {text}")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("slack_read error channel=%s", channel)
        return f"Erro ao ler canal: {exc}"
