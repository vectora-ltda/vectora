"""Ciclo de vida dos adapters de Connect.

Cada plataforma só sobe quando a credencial correspondente existe nos env
overrides do usuário **e** o usuário ligou o toggle explícito (`is_enabled`).
Melhor esforço, igual `nats_sidecar`/`electron_sidecar`: falha ao iniciar um
adapter é logada e não impede os outros nem o boot do backend — uma
credencial de Slack errada não pode derrubar o Vectora inteiro.

Antes desta feature, "credencial salva" sozinha já ligava a plataforma pra
sempre (achado ao vivo: um token de teste esquecido em `.env` subia o
client Discord silenciosamente, sem o usuário jamais ter pedido). O flag
`connect_enabled_platforms` (SQLite `app_settings` via `runtime_settings`,
mesmo backing store de `theme`/`language`) desacopla os dois — mas só a
partir do momento em que o usuário usa o toggle pela primeira vez
(`set_enabled()`). Até lá, `is_enabled()` espelha o comportamento antigo
(credencial presente = habilitado) — sem isso, o instante exato da
primeira leitura em produção (varia por processo) ou em teste (a ordem
de execução da suíte) decidiria pra sempre quais plataformas nascem
habilitadas, um cadeado de timing implícito e frágil. Ao primeiro toggle,
o override nasce preservando "ligado" pra tudo que já tinha credencial
configurada até aquele momento (não desliga silenciosamente uma
integração em uso só porque o usuário mexeu em OUTRA), e daí em diante é
a fonte de verdade — uma credencial nova não aparece aqui sozinha.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

#: Handle vivo por plataforma (Application do Telegram, Client do Discord,
#: handler do Slack, task do email). Vazio = nada rodando.
_running: dict[str, Any] = {}

_ENABLED_SETTINGS_KEY = "connect_enabled_platforms"


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def credentialed_platforms() -> set[str]:
    """Quais plataformas têm credencial completa agora (independente de
    tier e do toggle de enabled — só olha as env vars).

    Slack só entra com os **dois** tokens: com apenas um, o Socket Mode falha
    na conexão e o usuário veria um erro sem entender o que faltou.
    """
    platforms: set[str] = set()
    if _env("TELEGRAM_BOT_TOKEN"):
        platforms.add("telegram")
    if _env("DISCORD_BOT_TOKEN"):
        platforms.add("discord")
    if _env("SLACK_BOT_TOKEN") and _env("SLACK_APP_TOKEN"):
        platforms.add("slack")
    if (
        _env("EMAIL_IMAP_HOST")
        and _env("EMAIL_IMAP_USER")
        and _env("EMAIL_IMAP_PASSWORD")
    ):
        platforms.add("email")
    return platforms


def _enabled_overrides() -> dict[str, bool] | None:
    """`None` = o usuário nunca usou o toggle — nenhum override existe
    ainda. Um `dict` (mesmo vazio) = já existe pelo menos um override
    persistido; a partir daí ele é a fonte de verdade, não mais a
    credencial. `has()` distingue "nunca setado" de "setado vazio",
    diferente de `get()` (que sempre cairia num default)."""
    from backend.workspace.runtime_settings import runtime_settings

    if not runtime_settings.has(_ENABLED_SETTINGS_KEY):
        return None
    raw = runtime_settings.get(_ENABLED_SETTINGS_KEY, {})
    return raw if isinstance(raw, dict) else {}


def is_enabled(platform: str) -> bool:
    overrides = _enabled_overrides()
    if overrides is None:
        # Toggle nunca usado — mesmo comportamento de antes desta feature.
        return platform in credentialed_platforms()
    return bool(overrides.get(platform))


def set_enabled(platform: str, enabled: bool) -> None:
    """Persiste a preferência de enabled — não inicia/para nada sozinho,
    quem chama decide se reconcilia via `sync_adapters()` na sequência."""
    from backend.workspace.runtime_settings import runtime_settings

    overrides = _enabled_overrides()
    if overrides is None:
        # Primeiro toggle já usado nesta instância: preserva "ligado" pra
        # tudo que já tinha credencial configurada até agora — mexer numa
        # plataforma não pode desligar silenciosamente outra já em uso.
        overrides = dict.fromkeys(credentialed_platforms(), True)
    overrides[platform] = enabled
    runtime_settings.set(_ENABLED_SETTINGS_KEY, overrides)


def configured_platforms() -> set[str]:
    """Quais plataformas devem estar rodando agora: credencial + enabled.

    Sem tier pro, nenhuma plataforma sobe mesmo com credencial salva e
    enabled=true de antes de um downgrade — `sync_adapters()` reconcilia
    contra o conjunto vazio e desliga o que estiver rodando.
    """
    from backend.rbac.subscription import get_current_tier

    if get_current_tier() != "pro":
        return set()

    return {p for p in credentialed_platforms() if is_enabled(p)}


async def _start_platform(platform: str) -> Any:
    if platform == "telegram":
        from backend.services.connect import telegram

        return await telegram.start(_env("TELEGRAM_BOT_TOKEN"))
    if platform == "discord":
        from backend.services.connect import discord

        return await discord.start(_env("DISCORD_BOT_TOKEN"))
    if platform == "slack":
        from backend.services.connect import slack

        return await slack.start(_env("SLACK_BOT_TOKEN"), _env("SLACK_APP_TOKEN"))
    if platform == "email":
        from backend.services.connect import email

        config = email.EmailConfig(
            imap_host=_env("EMAIL_IMAP_HOST"),
            imap_user=_env("EMAIL_IMAP_USER"),
            imap_password=_env("EMAIL_IMAP_PASSWORD"),
            smtp_host=_env("EMAIL_SMTP_HOST") or _env("EMAIL_IMAP_HOST"),
        )
        return await email.start(config)
    msg = f"plataforma desconhecida: {platform!r}"
    raise ValueError(msg)


async def _stop_platform(platform: str, handle: Any) -> None:
    if platform == "telegram":
        from backend.services.connect import telegram

        await telegram.stop(handle)
    elif platform == "discord":
        from backend.services.connect import discord

        await discord.stop(handle)
    elif platform == "slack":
        from backend.services.connect import slack

        await slack.stop(handle)
    elif platform == "email":
        from backend.services.connect import email

        await email.stop(handle)


async def sync_adapters() -> dict[str, str]:
    """Reconcilia o que está rodando com o que está configurado.

    Chamado no boot e de novo quando o usuário salva credenciais nas Settings —
    é o mesmo caminho para ligar, desligar e reiniciar, então não há estado
    divergente entre "salvou o token" e "o bot está no ar".

    Devolve o status por plataforma (`started`/`stopped`/`running`/`failed:…`),
    consumível por um endpoint de diagnóstico.
    """
    desired = configured_platforms()
    status: dict[str, str] = {}

    for platform in sorted(set(_running) - desired):
        try:
            await _stop_platform(platform, _running.pop(platform))
            status[platform] = "stopped"
        except Exception as exc:
            logger.exception("connect.manager: falha ao parar %s", platform)
            status[platform] = f"failed: {exc}"

    for platform in sorted(desired):
        if platform in _running:
            status[platform] = "running"
            continue
        try:
            _running[platform] = await _start_platform(platform)
            status[platform] = "started"
        except Exception as exc:
            # Um adapter que não sobe (dependência ausente, token inválido)
            # nunca impede os outros nem o boot do backend.
            logger.warning("connect.manager: %s não iniciou (%s)", platform, exc)
            status[platform] = f"failed: {exc}"

    return status


async def stop_all() -> None:
    """Desliga tudo no shutdown do backend. Idempotente."""
    for platform in list(_running):
        try:
            await _stop_platform(platform, _running.pop(platform))
        except Exception:
            logger.exception("connect.manager: falha ao parar %s", platform)


def running_platforms() -> set[str]:
    return set(_running)
