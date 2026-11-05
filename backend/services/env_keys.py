"""Aplicação em runtime de API keys de LLM/search — fonte única compartilhada.

Evita a duplicação que causava o bug: `PATCH /admin/api-keys` e `POST /envs`
tinham cada um sua própria cópia da lógica "grava no .env + atualiza
os.environ + atualiza settings", e só a primeira estava correta — a
segunda gravava só no banco por-usuário, nunca chegando em `os.environ`.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: Env vars de LLM/search conhecidas pelo Vectora — qualquer uma delas
#: setada via `/envs` (não só via `/admin/api-keys`) precisa valer
#: imediatamente na próxima chamada ao provider, sem restart.
KNOWN_LLM_ENV_KEYS: frozenset[str] = frozenset(
    {
        "GOOGLE_API_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "COHERE_API_KEY",
        "TAVILY_API_KEY",
        "OPENROUTER_API_KEY",
    }
)

#: Credenciais das plataformas do Vectora Connect. Seguem o mesmo caminho das
#: keys de LLM (arquivo + `os.environ`) porque os adapters rodam dentro do
#: processo do backend e leem `os.environ` — sem isso, salvar o token na UI
#: gravaria o override no banco e o bot continuaria offline até o próximo boot.
CONNECT_ENV_KEYS: frozenset[str] = frozenset(
    {
        "TELEGRAM_BOT_TOKEN",
        "DISCORD_BOT_TOKEN",
        "SLACK_BOT_TOKEN",
        "SLACK_APP_TOKEN",
        "EMAIL_IMAP_HOST",
        "EMAIL_IMAP_USER",
        "EMAIL_IMAP_PASSWORD",
        "EMAIL_SMTP_HOST",
    }
)

#: Credenciais lidas por tools que rodam dentro do processo do backend. Mesmo
#: motivo do conjunto acima: sem chegar em `os.environ`/`settings`, a tool
#: continuaria se declarando "não configurada" depois de o usuário salvar.
TOOL_ENV_KEYS: frozenset[str] = frozenset(
    {
        "HOME_ASSISTANT_URL",
        "HOME_ASSISTANT_TOKEN",
    }
)

#: Tudo que precisa valer na próxima chamada, sem esperar o próximo boot.
RUNTIME_ENV_KEYS: frozenset[str] = KNOWN_LLM_ENV_KEYS | CONNECT_ENV_KEYS | TOOL_ENV_KEYS


def default_env_file() -> Path:
    """``settings.vectora_home / ".env"`` — mesmo arquivo que `PATCH /admin/api-keys` já usa."""
    from backend.settings import settings

    p = settings.vectora_home / ".env"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def apply_llm_env_key(env_file: Path, env_var: str, value: str) -> None:
    """Persiste ``env_var=value`` em ``env_file``, ``os.environ`` e ``settings``.

    Nunca loga o valor — só o nome da env var (padrão de auditoria já usado
    em `patch_api_keys`). ``value`` vazia limpa a key (mesmo comportamento
    de `patch_api_keys`, que aceita string vazia pra "esquecer" uma key).
    """
    from backend.cli.keys import upsert_env_key

    v = value.strip()
    upsert_env_key(env_file, env_var, v)
    os.environ[env_var] = v

    try:
        from backend.settings import settings

        attr = env_var.lower()
        if hasattr(settings, attr):
            object.__setattr__(settings, attr, v or None)
    except Exception:
        logger.exception("env_keys: falha ao atualizar settings.%s", env_var.lower())
