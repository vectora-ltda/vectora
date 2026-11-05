"""``vectora config`` — configuração do aplicativo.

Sem ação, mostra o estado completo da configuração (LLM, API keys, storage).
Com ação, despacha para o fluxo correspondente:

  - ``config keys``               — wizard de API keys + LLM provider
  - ``config docker [up|down|status]`` — infra local via Docker
  - ``config qdrant <url> [--api-key]`` — testa e persiste Qdrant
  - ``config redis <url>``        — testa e persiste Redis
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from backend.settings import settings

# ---------------------------------------------------------------------------
# Mapeamento de modelos → providers (para auto-detecção)
# ---------------------------------------------------------------------------

_MODEL_TO_PROVIDER: list[tuple[str, str]] = [
    ("gemini-", "google-genai"),
    ("gpt-", "openai"),
    ("o1-", "openai"),
    ("o3-", "openai"),
    ("o4-", "openai"),
    ("claude-", "anthropic"),
    ("command-", "cohere"),
    ("llama", "ollama"),
    ("mistral", "ollama"),
    ("qwen", "ollama"),
    ("phi", "ollama"),
    ("deepseek", "ollama"),
    ("codellama", "ollama"),
    ("tinyllama", "ollama"),
    ("vicuna", "ollama"),
]

_PROVIDER_MODELS: dict[str, list[str]] = {
    "google-genai": [
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-2.5-pro",
    ],
    "openai": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "o1-preview", "o4-mini"],
    "anthropic": [
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5-20251001",
    ],
    "cohere": [
        "command-a-03-2025",
        "command-r-plus-08-2024",
        "command-r-08-2024",
    ],
    "ollama": [
        "llama3.3",
        "qwen2.5:32b",
        "mistral",
        "codellama",
        "(qualquer modelo local)",
    ],
}

# Chaves que vão para ~/.vectora/.env
_ENV_KEYS: dict[str, str] = {
    "postgres_dsn": "POSTGRES_DSN",
    "redis_url": "REDIS_URL",
    "qdrant_url": "QDRANT_URL",
    "qdrant_api_key": "QDRANT_API_KEY",
    "google_api_key": "GOOGLE_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "cohere_api_key": "COHERE_API_KEY",
    "tavily_api_key": "TAVILY_API_KEY",
}

# Chaves que vão para settings.json via runtime_settings
_SETTINGS_KEYS = {"active_provider", "active_model", "storage_mode"}

_ALL_KEYS = set(_ENV_KEYS) | _SETTINGS_KEYS


def _detect_provider(model: str) -> str | None:
    """Infere o provider a partir do nome do modelo."""
    lower = model.lower()
    for prefix, provider in _MODEL_TO_PROVIDER:
        if lower.startswith(prefix):
            return provider
    return None


def _mask_dsn(value: str | None) -> str:
    """Oculta senha em DSNs: user:PASS@host → user:***@host."""
    if not value:
        return "—"
    return re.sub(r"://([^:@]+):[^@]+@", r"://\1:***@", value)


def _mask_key(value: str | None) -> str:
    """Oculta API key: mostra só os últimos 4 caracteres."""
    if not value:
        return "—"
    if len(value) <= 8:
        return "***"
    return f"***{value[-4:]}"


def _env_file() -> Path:
    return settings.vectora_home / ".env"


def _write_env(key: str, value: str) -> None:
    from backend.cli.keys import upsert_env_key

    env = _env_file()
    env.parent.mkdir(parents=True, exist_ok=True)
    upsert_env_key(env, key, value)


# ---------------------------------------------------------------------------
# --set: aplicar valores
# ---------------------------------------------------------------------------


def _apply_set_values(set_values: list[str]) -> None:
    """Processa ``--set KEY=VALUE`` e persiste nos arquivos corretos."""
    from backend.workspace.runtime_settings import runtime_settings

    for kv in set_values:
        if "=" not in kv:
            print(f"❌ Formato inválido '{kv}'. Use KEY=VALUE.")
            sys.exit(1)
        key, _, value = kv.partition("=")
        key = key.strip().lower()
        value = value.strip()

        if key not in _ALL_KEYS:
            print(
                f"❌ Chave desconhecida '{key}'.\n"
                f"   Chaves válidas: {', '.join(sorted(_ALL_KEYS))}"
            )
            sys.exit(1)

        if key in _ENV_KEYS:
            _write_env(_ENV_KEYS[key], value)
            print(
                f"✓ {_ENV_KEYS[key]}={_mask_dsn(value) if 'dsn' in key or 'url' in key else _mask_key(value)}"
            )

        if key == "storage_mode":
            if value not in ("lite", "complete"):
                print("❌ storage_mode deve ser 'lite' ou 'complete'.")
                sys.exit(1)
            runtime_settings.set("storage_mode", value)
            print(f"✓ storage_mode={value}")

        elif key == "active_model":
            provider = _detect_provider(value) or runtime_settings.active_provider
            runtime_settings.set_active_model(provider, value)
            print(f"✓ active_model={value}  →  active_provider={provider}")

        elif key == "active_provider":
            runtime_settings.set("active_provider", value)
            print(f"✓ active_provider={value}")


# ---------------------------------------------------------------------------
# Display completo
# ---------------------------------------------------------------------------


def _show_or_set(args: argparse.Namespace) -> None:
    """``vectora config`` (sem ação) — mostra configuração completa ou edita."""
    set_values = getattr(args, "set_values", None)
    if set_values:
        _apply_set_values(set_values)
        return

    from rich.console import Console
    from rich.table import Table

    from backend.settings import Settings
    from backend.workspace.runtime_settings import runtime_settings

    console = Console()
    settings = Settings()

    # ── LLM ──────────────────────────────────────────────────────────
    llm = Table(title="LLM", show_lines=False, expand=False, box=None)
    llm.add_column("Chave", style="cyan bold", width=18)
    llm.add_column("Valor")

    active_provider = runtime_settings.active_provider
    active_model = runtime_settings.active_model
    llm.add_row("active_provider", active_provider)
    llm.add_row("active_model", active_model)
    console.print(llm)

    console.print()
    console.print("[dim]Modelos disponíveis:[/dim]")
    for provider, models in _PROVIDER_MODELS.items():
        marker = " [bold green]◀[/bold green]" if provider == active_provider else ""
        console.print(f"  [cyan]{provider:<14}[/cyan] {', '.join(models)}{marker}")

    # ── API Keys ─────────────────────────────────────────────────────
    console.print()
    keys_tbl = Table(
        title="API Keys  (~/.vectora/.env)", show_lines=False, expand=False, box=None
    )
    keys_tbl.add_column("Variável", style="cyan bold", width=22)
    keys_tbl.add_column("Status", width=20)
    keys_tbl.add_column("Uso", style="dim")

    key_rows = [
        ("GOOGLE_API_KEY", settings.google_api_key, "Gemini (LLM)"),
        ("OPENAI_API_KEY", settings.openai_api_key, "GPT-4o (LLM)"),
        ("ANTHROPIC_API_KEY", settings.anthropic_api_key, "Claude (LLM)"),
        ("COHERE_API_KEY", settings.cohere_api_key, "Embeddings + Reranking + LLM"),
        ("TAVILY_API_KEY", settings.tavily_api_key, "Busca web"),
    ]
    for env_var, val, usage in key_rows:
        if val:
            status = f"[green]✓ {_mask_key(val)}[/green]"
        else:
            status = "[dim]— não configurada[/dim]"
        keys_tbl.add_row(env_var, status, usage)
    console.print(keys_tbl)

    # ── Storage ───────────────────────────────────────────────────────
    console.print()
    storage_tbl = Table(
        title="Storage  (~/.vectora/.env)", show_lines=False, expand=False, box=None
    )
    storage_tbl.add_column("Chave", style="cyan bold", width=18)
    storage_tbl.add_column("Valor")
    storage_tbl.add_column("Descrição", style="dim")

    storage_mode = runtime_settings.storage_mode
    storage_tbl.add_row(
        "storage_mode",
        f"[bold]{storage_mode}[/bold]",
        "lite (SQLite+LanceDB) | complete (Postgres+Redis+Qdrant)",
    )
    storage_tbl.add_row(
        "POSTGRES_DSN", _mask_dsn(settings.postgres_dsn), "modo complete"
    )
    storage_tbl.add_row("REDIS_URL", settings.redis_url or "—", "modo complete")
    storage_tbl.add_row("QDRANT_URL", settings.qdrant_url or "—", "modo complete")
    if settings.qdrant_api_key:
        storage_tbl.add_row(
            "QDRANT_API_KEY", _mask_key(settings.qdrant_api_key), "Qdrant Cloud"
        )
    console.print(storage_tbl)

    # ── Startup mode ─────────────────────────────────────────────────
    import os

    is_desktop = bool(os.environ.get("VECTORA_DESKTOP"))
    is_headless = bool(os.environ.get("VECTORA_HEADLESS"))
    if is_desktop:
        start_mode = "desktop (Electron/tray)"
    elif is_headless:
        start_mode = "headless (bandeja sem janela)"
    else:
        start_mode = "server (web/VPS)"

    console.print()
    console.print(f"[dim]Modo de startup:[/dim] {start_mode}")
    console.print(f"[dim]Settings:[/dim]  {settings.vectora_home / 'settings.json'}")
    console.print(f"[dim]Env file:[/dim]  {_env_file()}")

    # ── Subcommands ───────────────────────────────────────────────────
    console.print()
    console.print("[bold]Comandos disponíveis:[/bold]")
    cmds = [
        ("vectora config keys", "wizard interativo: API keys + LLM provider"),
        ("vectora config docker up", "sobe Postgres + Redis + Qdrant local"),
        ("vectora config docker down", "para a infra local"),
        ("vectora config docker status", "status da infra local"),
        ("vectora config qdrant <url>", "configura e testa Qdrant"),
        ("vectora config redis <url>", "configura e testa Redis"),
        ("vectora config --set KEY=VALUE", "edita uma chave diretamente"),
        ("vectora config integrations", "API keys de LLM/search (--get/--set)"),
        ("vectora config connect", "tokens de bot de mensageria (--get/--set)"),
        ("vectora config preferences", "tema/idioma/timezone/etc (--get/--set)"),
        ("vectora auth login", "autentica no servidor Vectora"),
        ("vectora storage info", "status de saúde de todos os backends"),
    ]
    for cmd, desc in cmds:
        console.print(f"  [cyan]{cmd:<42}[/cyan] {desc}")

    console.print()
    console.print("[dim]Chaves editáveis via --set:[/dim]")
    console.print("  [dim]LLM:     [/dim] active_provider, active_model")
    console.print(
        "  [dim]Storage: [/dim] storage_mode, postgres_dsn, redis_url, qdrant_url, qdrant_api_key"
    )
    console.print(
        "  [dim]API Keys:[/dim] google_api_key, openai_api_key, anthropic_api_key, "
        "cohere_api_key, tavily_api_key"
    )
    console.print()
    console.print(
        "[dim]Para iniciar como servidor (VPS):[/dim] [cyan]vectora start --headless[/cyan]"
    )


# ---------------------------------------------------------------------------
# Categorias do registry declarativo (backend/config/registry.py) — mesmas
# categorias das abas do frontend (Ambiente/Preferências). Get/set genéricos
# aqui; recursos em formato de coleção (provider-routing, memory, account)
# continuam com seus próprios comandos, não entram nesta lista — ver
# docstring de backend/config/registry.py.
# ---------------------------------------------------------------------------

_REGISTRY_CATEGORIES = frozenset({"integrations", "connect", "preferences"})


def _run_category_command(category: str, args: argparse.Namespace) -> None:
    """``vectora config <categoria> [--get KEY]... [--set KEY=VALUE]...`` —
    despacha pro registry declarativo em vez do ``_ALL_KEYS``/``_apply_set_values``
    fixo usado pela chamada sem ação (LLM/storage legado)."""
    from backend.config import fields_for_category, get_field

    get_values: list[str] = getattr(args, "get_values", None) or []
    set_values: list[str] = getattr(args, "set_values", None) or []

    if not get_values and not set_values:
        fields = fields_for_category(category)
        if not fields:
            print(f"Nenhum campo registrado na categoria '{category}'.")
            return
        print(f"Campos de '{category}':")
        for f in fields:
            if f.secret:
                raw = f.get()
                shown = _mask_key(str(raw)) if raw else "— não configurada"
            else:
                shown = f.get()
            print(f"  {f.key:<24} {shown}   ({f.cli_flag})")
        return

    for key in get_values:
        field = get_field(key.strip().lower())
        if field is None or field.category != category:
            print(f"❌ Chave '{key}' não existe na categoria '{category}'.")
            sys.exit(1)
        raw = field.get()
        shown = _mask_key(str(raw)) if field.secret and raw else raw
        print(f"{field.key}={shown}")

    for kv in set_values:
        if "=" not in kv:
            print(f"❌ Formato inválido '{kv}'. Use KEY=VALUE.")
            sys.exit(1)
        key, _, value = kv.partition("=")
        field = get_field(key.strip().lower())
        if field is None or field.category != category:
            print(f"❌ Chave '{key}' não existe na categoria '{category}'.")
            sys.exit(1)
        field.set(value.strip())
        shown = _mask_key(value.strip()) if field.secret else value.strip()
        print(f"✓ {field.key}={shown}")


# ---------------------------------------------------------------------------
# Categorias de coleção (backend/config/collections.py) — recursos que são
# listas, não pares chave→valor: modelos registrados por gateway (global) e
# memórias/perfil de conta (por usuário, sentinela "local" no desktop —
# mesmo sentinela já usado por rbac.auth.get_env_overrides/set_env_override).
# ---------------------------------------------------------------------------

_COLLECTION_ACTION_TO_CATEGORY = {
    "provider-routing": "provider_routing",
    "memory": "memory",
    "account": "account",
}
_USER_SCOPED_CATEGORIES = frozenset({"memory", "account"})


def _run_collection_command(action: str, args: argparse.Namespace) -> None:
    """``vectora config provider-routing|memory|account --list`` — lista os
    itens de um recurso de coleção do schema declarativo. Sem escrita: add/
    remove de coleção continuam pelos comandos/endpoints especializados que
    já existiam antes do registry (CRUD real, não par chave→valor)."""
    import asyncio

    from backend.config import collections_for_category, user_scoped_fields_for_category

    if not getattr(args, "list_collection", False):
        print(f"❌ Use --list para listar os itens de '{action}'.")
        sys.exit(1)

    category = _COLLECTION_ACTION_TO_CATEGORY[action]

    if category in _USER_SCOPED_CATEGORIES:
        user_id = getattr(args, "user_id", None) or "local"
        fields = user_scoped_fields_for_category(category)
        if not fields:
            print(f"Nenhum recurso de coleção registrado na categoria '{category}'.")
            return
        for field in fields:
            items = asyncio.run(field.list_items(user_id))
            print(f"{field.key} (user_id={user_id}):")
            if not items:
                print("  (nenhum item)")
            for item in items:
                print(f"  {item}")
        return

    fields = collections_for_category(category)
    if not fields:
        print(f"Nenhum recurso de coleção registrado na categoria '{category}'.")
        return
    for field in fields:
        items = asyncio.run(field.list_items())
        print(f"{field.key}:")
        if not items:
            print("  (nenhum item)")
        for item in items:
            print(f"  {item}")


# ---------------------------------------------------------------------------
# Dispatcher principal
# ---------------------------------------------------------------------------


def run_config(args: argparse.Namespace) -> None:
    """Despacha ``vectora config [ação]``."""
    action = getattr(args, "config_action", None)

    if action is None:
        _show_or_set(args)
        return

    if action in _REGISTRY_CATEGORIES or action in _COLLECTION_ACTION_TO_CATEGORY:
        if action in _REGISTRY_CATEGORIES:
            _run_category_command(action, args)
        else:
            _run_collection_command(action, args)
        return

    if action == "keys":
        from backend.cli.keys import run_keys

        run_keys()
        return

    if action == "docker":
        from backend.cli.infra import run_docker

        run_docker(getattr(args, "config_arg", None) or "status")
        return

    if action == "qdrant":
        from backend.cli.infra import run_qdrant

        run_qdrant(
            getattr(args, "config_arg", None) or "",
            getattr(args, "api_key", None),
        )
        return

    if action == "redis":
        from backend.cli.infra import run_redis

        run_redis(getattr(args, "config_arg", None) or "")
        return

    print(f"❌ Ação de config desconhecida: {action!r}")
    sys.exit(1)
