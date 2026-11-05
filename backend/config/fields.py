"""Definição dos campos de configuração escalares expostos pelo registry
declarativo (``backend.config.registry``). Importar este módulo é o que
popula o registry — feito uma vez em ``backend/config/__init__.py``.

Categorias cobertas aqui: ``integrations`` (API keys de LLM/search),
``connect`` (tokens de bot de mensageria), ``preferences`` (tema/idioma/
timezone/ordem de fallback). ``provider_routing`` (modelos registrados por
gateway), ``memory`` (CRUD de memórias) e ``account`` (perfil do usuário) não
entram aqui — são coleções/recursos, não pares chave→valor escalares; ver
docstring de ``registry.py``.
"""

from __future__ import annotations

from backend.config.adapters import (
    ConfigTomlAdapter,
    EnvAdapter,
    RuntimeSettingsAdapter,
)
from backend.config.registry import setting_field

# ── integrations — API keys de LLM/search (backend/services/env_keys.py::KNOWN_LLM_ENV_KEYS) ──

setting_field(
    "google_api_key",
    category="integrations",
    cli_flag="--google-api-key",
    description="API key do Google Gemini.",
    adapter=EnvAdapter("GOOGLE_API_KEY"),
    secret=True,
)
setting_field(
    "openai_api_key",
    category="integrations",
    cli_flag="--openai-api-key",
    description="API key da OpenAI.",
    adapter=EnvAdapter("OPENAI_API_KEY"),
    secret=True,
)
setting_field(
    "anthropic_api_key",
    category="integrations",
    cli_flag="--anthropic-api-key",
    description="API key da Anthropic.",
    adapter=EnvAdapter("ANTHROPIC_API_KEY"),
    secret=True,
)
setting_field(
    "cohere_api_key",
    category="integrations",
    cli_flag="--cohere-api-key",
    description="API key da Cohere (embeddings/reranker/RAG).",
    adapter=EnvAdapter("COHERE_API_KEY"),
    secret=True,
)
setting_field(
    "tavily_api_key",
    category="integrations",
    cli_flag="--tavily-api-key",
    description="API key da Tavily (busca web).",
    adapter=EnvAdapter("TAVILY_API_KEY"),
    secret=True,
)
setting_field(
    "openrouter_api_key",
    category="integrations",
    cli_flag="--openrouter-api-key",
    description="API key do OpenRouter.",
    adapter=EnvAdapter("OPENROUTER_API_KEY"),
    secret=True,
)

setting_field(
    "oidc_client_id",
    category="integrations",
    cli_flag="--oidc-client-id",
    description="Client ID do provedor OIDC (SSO) configurado.",
    adapter=EnvAdapter("OIDC_CLIENT_ID"),
)
setting_field(
    "oidc_client_secret",
    category="integrations",
    cli_flag="--oidc-client-secret",
    description="Client secret do provedor OIDC (SSO) configurado.",
    adapter=EnvAdapter("OIDC_CLIENT_SECRET"),
    secret=True,
)
setting_field(
    "oidc_issuer_url",
    category="integrations",
    cli_flag="--oidc-issuer-url",
    description="Issuer URL do provedor OIDC (SSO) — usado pra descoberta "
    "via `.well-known/openid-configuration`.",
    adapter=EnvAdapter("OIDC_ISSUER_URL"),
)

# ── connect — tokens de bot de mensageria (backend/services/env_keys.py::CONNECT_ENV_KEYS) ──

setting_field(
    "telegram_bot_token",
    category="connect",
    cli_flag="--telegram-bot-token",
    description="Bot Token do Telegram (Vectora Connect).",
    adapter=EnvAdapter("TELEGRAM_BOT_TOKEN"),
    secret=True,
)
setting_field(
    "discord_bot_token",
    category="connect",
    cli_flag="--discord-bot-token",
    description="Bot Token do Discord (Vectora Connect).",
    adapter=EnvAdapter("DISCORD_BOT_TOKEN"),
    secret=True,
)
setting_field(
    "slack_bot_token",
    category="connect",
    cli_flag="--slack-bot-token",
    description="Bot Token do Slack (Vectora Connect).",
    adapter=EnvAdapter("SLACK_BOT_TOKEN"),
    secret=True,
)
setting_field(
    "slack_app_token",
    category="connect",
    cli_flag="--slack-app-token",
    description="App Token do Slack (Socket Mode, Vectora Connect).",
    adapter=EnvAdapter("SLACK_APP_TOKEN"),
    secret=True,
)
setting_field(
    "email_imap_host",
    category="connect",
    cli_flag="--email-imap-host",
    description="Host IMAP da caixa de entrada monitorada (Vectora Connect).",
    adapter=EnvAdapter("EMAIL_IMAP_HOST"),
)
setting_field(
    "email_imap_user",
    category="connect",
    cli_flag="--email-imap-user",
    description="Usuário IMAP da caixa de entrada monitorada (Vectora Connect).",
    adapter=EnvAdapter("EMAIL_IMAP_USER"),
)
setting_field(
    "email_imap_password",
    category="connect",
    cli_flag="--email-imap-password",
    description="Senha/app-password IMAP (Vectora Connect).",
    adapter=EnvAdapter("EMAIL_IMAP_PASSWORD"),
    secret=True,
)
setting_field(
    "email_smtp_host",
    category="connect",
    cli_flag="--email-smtp-host",
    description="Host SMTP para envio de resposta (Vectora Connect); "
    "se omitido, usa o mesmo host do IMAP.",
    adapter=EnvAdapter("EMAIL_SMTP_HOST"),
)

# ── preferences — RuntimeSettings global (SQLite app_settings) ──

setting_field(
    "theme",
    category="preferences",
    cli_flag="--theme",
    description="Tema da interface ('dark'|'light'|'system').",
    adapter=RuntimeSettingsAdapter(),
)
setting_field(
    "language",
    category="preferences",
    cli_flag="--language",
    description="Idioma da interface ('en'|'es'|'pt-BR').",
    adapter=RuntimeSettingsAdapter(),
)
setting_field(
    "timezone",
    category="preferences",
    cli_flag="--timezone",
    description="Timezone IANA usado no agendamento de tarefas (ex.: America/Sao_Paulo).",
    adapter=RuntimeSettingsAdapter(settings_key="user_timezone"),
)
setting_field(
    "image_fallback_model",
    category="preferences",
    cli_flag="--image-fallback-model",
    description=(
        "Modelo usado automaticamente quando o modelo ativo não processa "
        "imagem e a mensagem tem anexo (formato 'provider:model', ex.: "
        "'google-genai:gemini-2.5-flash'). Vazio = comportamento antigo "
        "(bloqueia o envio com aviso)."
    ),
    adapter=RuntimeSettingsAdapter(),
)

# ── admin/config (~/.vectora/config.toml [server]) — hoje só via /admin/config REST ──

setting_field(
    "default_model",
    category="preferences",
    cli_flag="--default-model",
    description="Modelo padrão do servidor para novas sessões.",
    adapter=ConfigTomlAdapter("server"),
)
setting_field(
    "allow_public_signup",
    category="preferences",
    cli_flag="--allow-public-signup",
    description="Permite cadastro público sem convite (instâncias multi-usuário).",
    adapter=ConfigTomlAdapter("server"),
)
