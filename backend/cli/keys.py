"""``vectora config keys`` — configuração interativa de API keys e LLM provider.

Fluxo em 3 etapas:
  1. Cohere API key  — opcional (embeddings + reranking RAG; sem ela, Ollama
                       cobre embeddings — ver backend/storage/factory.py —
                       e o RAG roda sem reranking)
  2. Tavily API key  — opcional (busca web em tempo real; sem ela, cai no
                       fallback sem chave via backend/browser/search_fallback.py)
  3. LLM provider    — Gemini (free), Cohere (free, usa key acima), OpenAI (paid),
                       Anthropic (paid), Ollama (local, sem key)
     └─ Ollama: pede o nome do model que o usuário já tem instalado

Ao final testa a conexão com o LLM e persiste em ``~/.vectora/.env`` +
``~/.vectora/settings.json``.
"""

from __future__ import annotations

import asyncio
import getpass
import logging
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from backend.settings import settings

logger = logging.getLogger(__name__)

LLM_PROVIDERS: dict[str, dict[str, str]] = {
    "1": {
        "name": "Google Gemini",
        "tier": "free",
        "provider_id": "google-genai",
        "env_var": "GOOGLE_API_KEY",
        "url": "https://aistudio.google.com/app/apikey",
        "default_model": "gemini-3.6-flash",
    },
    "2": {
        "name": "Cohere",
        "tier": "free",
        "provider_id": "cohere",
        "env_var": "COHERE_API_KEY",
        "url": "https://dashboard.cohere.com/api-keys",
        "default_model": "command-a-03-2025",
    },
    "3": {
        "name": "OpenAI",
        "tier": "paid",
        "provider_id": "openai",
        "env_var": "OPENAI_API_KEY",
        "url": "https://platform.openai.com/api-keys",
        "default_model": "gpt-4o",
    },
    "4": {
        "name": "Anthropic Claude",
        "tier": "paid",
        "provider_id": "anthropic",
        "env_var": "ANTHROPIC_API_KEY",
        "url": "https://console.anthropic.com/",
        "default_model": "claude-sonnet-4-6",
    },
    "5": {
        "name": "Ollama (Local)",
        "tier": "local",
        "provider_id": "ollama",
        "env_var": "",
        "url": "https://ollama.ai",
        "default_model": "",
    },
}


# ---------------------------------------------------------------------------
# Persistência (~/.vectora/.env e settings.json)
# ---------------------------------------------------------------------------


def upsert_env_key(env_file: Path, key: str, value: str) -> None:
    """Insere ou atualiza uma ``KEY=value`` no arquivo .env (idempotente)."""
    lines: list[str] = []
    found = False
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    content = "\n".join(lines) + "\n"

    import os
    import tempfile

    fd, temp_path_str = tempfile.mkstemp(dir=env_file.parent, prefix=".env.tmp")
    temp_path = Path(temp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        temp_path.replace(env_file)
    except Exception:
        temp_path.unlink()
        raise


def save_keys_to_env(keys: dict[str, str]) -> None:
    """Salva as API keys em ``~/.vectora/.env``."""
    env_file = settings.vectora_home / ".env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    for key, value in keys.items():
        if value:
            upsert_env_key(env_file, key, value)


def _save_provider_to_settings(provider_id: str, model: str) -> None:
    """Salva provider e model ativos em ``~/.vectora/settings.json``."""
    from backend.workspace.runtime_settings import runtime_settings

    runtime_settings.set_active_model(provider_id, model)
    logger.info("Provider salvo", extra={"provider": provider_id, "model": model})


# ---------------------------------------------------------------------------
# Teste de conexão
# ---------------------------------------------------------------------------


def _load_llm_for_test(provider_id: str, model: str, api_key: str | None) -> Any:
    """Instancia o LLM correto para o teste de conexão."""
    if provider_id == "google-genai":
        from backend.llm.google.chat_client import GoogleChatClient
        from backend.llm.google.client import GoogleGenAIClient

        return GoogleChatClient(
            model=model, client=GoogleGenAIClient(api_key=api_key or "")
        )

    if provider_id == "openai":
        from backend.llm.openai.chat_client import OpenAIChatClient
        from backend.llm.openai.client import OpenAIClient

        return OpenAIChatClient(model=model, client=OpenAIClient(api_key=api_key or ""))

    if provider_id == "anthropic":
        from backend.llm.anthropic.chat_client import AnthropicChatClient
        from backend.llm.anthropic.client import AnthropicClient

        return AnthropicChatClient(
            model=model, client=AnthropicClient(api_key=api_key or "")
        )

    if provider_id == "cohere":
        from backend.llm.cohere.chat_client import CohereChatClient
        from backend.llm.cohere.client import CohereClient

        return CohereChatClient(model=model, client=CohereClient(api_key=api_key or ""))

    if provider_id == "ollama":
        from backend.llm.ollama.chat_client import OllamaChatClient
        from backend.llm.ollama.client import OllamaClient

        return OllamaChatClient(model=model, client=OllamaClient())

    msg = f"Provider desconhecido: {provider_id}"
    raise ValueError(msg)


async def _test_connection(
    console: Console,
    provider_id: str,
    model: str,
    api_key: str | None,
) -> None:
    """Testa a conexão com o LLM escolhido."""
    console.print("[bold]Testando conexão...[/bold]\n")

    try:
        llm = _load_llm_for_test(provider_id, model, api_key)
        with console.status(
            "[bold cyan]Conectando ao LLM...[/bold cyan]", spinner="dots"
        ):
            if hasattr(llm, "ainvoke"):
                response = await llm.ainvoke("Say 'Connected!' in one word.")
                resposta_texto = response.content
            else:
                # ChatClient nativo (Protocol, ex. CohereChatClient) — sem
                # `ainvoke`, opera sobre `VMessage`/`agenerate` em vez do
                # `BaseChatModel` dos demais providers testados aqui.
                from backend.vtypes.message import MessageRole, text_message

                resultado = await llm.agenerate(
                    [text_message(MessageRole.USER, "Say 'Connected!' in one word.")]
                )
                resposta_texto = resultado.text()
        console.print(
            Panel(
                f"[green]✓ Conexão bem-sucedida![/green]\n"
                f"[cyan]Resposta: {resposta_texto}[/cyan]",
                title="[bold green]Teste de Conexão[/bold green]",
                style="green",
                expand=False,
            )
        )
        console.print()
    except Exception as exc:
        console.print(
            Panel(
                f"[red]{exc!s}[/red]",
                title="[bold red]✗ Conexão Falhou[/bold red]",
                style="red",
            )
        )
        logger.exception("Teste de conexão falhou")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Etapas do wizard
# ---------------------------------------------------------------------------


async def _step_cohere_key(console: Console) -> str:
    """Coleta a Cohere API key, opcional (embeddings + reranking do RAG)."""
    console.print(
        Rule("[bold cyan]Passo 1 de 3 — Cohere API Key[/bold cyan]", style="cyan")
    )
    console.print(
        "\n[bold]Cohere é opcional[/bold] — fornece os embeddings e o reranker "
        "usados pelo RAG (busca vetorial semântica). Deixe em branco para "
        "pular — se você for usar [bold]Ollama[/bold] no próximo passo, ele "
        "cobre os embeddings sozinho (sem reranking).\n"
        "Plano gratuito: [cyan]https://dashboard.cohere.com/api-keys[/cyan]\n"
    )
    api_key = getpass.getpass("Cohere API key (oculta, Enter para pular): ").strip()
    if not api_key:
        console.print("[yellow]○ Cohere pulado.[/yellow]\n")
        return ""
    console.print("[green]✓ Cohere key recebida.[/green]\n")
    return api_key


async def _step_tavily_key(console: Console) -> str:
    """Coleta a Tavily API key, opcional (busca web em tempo real)."""
    console.print(
        Rule("[bold cyan]Passo 2 de 3 — Tavily API Key[/bold cyan]", style="cyan")
    )
    console.print(
        "\n[bold]Tavily é opcional[/bold] — busca web em tempo real e extração "
        "de conteúdo de URLs. Deixe em branco para pular — o Vectora cai num "
        "fallback sem chave (API do DuckDuckGo + Chromium para extração de "
        "URLs), com cobertura menor que o Tavily.\n"
        "Plano gratuito: [cyan]https://tavily.com[/cyan]\n"
    )
    api_key = getpass.getpass("Tavily API key (oculta, Enter para pular): ").strip()
    if not api_key:
        console.print("[yellow]○ Tavily pulado — usando fallback sem chave.[/yellow]\n")
        return ""
    console.print("[green]✓ Tavily key recebida.[/green]\n")
    return api_key


async def _step_select_llm(
    console: Console, cohere_key: str
) -> tuple[str, str, str | None]:
    """Seleciona o provider de LLM e coleta model/key.

    Returns:
        ``(provider_id, model, api_key_or_None)``.
    """
    console.print(
        Rule("[bold cyan]Passo 3 de 3 — Provider de LLM[/bold cyan]", style="cyan")
    )
    console.print("\n[bold]Escolha o modelo de linguagem:[/bold]\n")

    table = Table(show_header=True, header_style="bold cyan", box=None, padding=(0, 2))
    table.add_column("#", style="dim", width=3)
    table.add_column("Provider", style="bold")
    table.add_column("Plano", style="yellow")
    table.add_column("Model padrão")

    tier_labels = {
        "free": "[green]gratuito[/green]",
        "paid": "[red]pago[/red]",
        "local": "[blue]local[/blue]",
    }
    for key, info in LLM_PROVIDERS.items():
        model_display = info["default_model"] or "[dim]você define[/dim]"
        table.add_row(key, info["name"], tier_labels[info["tier"]], model_display)

    console.print(table)
    console.print()

    provider_choice = ""
    while provider_choice not in LLM_PROVIDERS:
        raw = (await asyncio.to_thread(input, "Escolha o provider (1-5): ")).strip()
        if raw in LLM_PROVIDERS:
            provider_choice = raw
        else:
            console.print("[red]Opção inválida. Escolha entre 1 e 5.[/red]")

    provider_info = LLM_PROVIDERS[provider_choice]
    provider_id = provider_info["provider_id"]
    console.print(f"\n[green]✓ {provider_info['name']} selecionado.[/green]\n")

    if provider_id == "ollama":
        console.print(
            "[bold]Ollama[/bold] usa modelos instalados localmente.\n"
            "Execute [cyan]ollama list[/cyan] para ver os disponíveis.\n"
            "Exemplos: [dim]llama3:8b  mistral  codellama  qwen2.5:7b[/dim]\n"
        )
        model = (await asyncio.to_thread(input, "Nome do model Ollama: ")).strip()
        if not model:
            console.print("[red]Nome do model é obrigatório para Ollama.[/red]")
            sys.exit(1)
        console.print(f"[green]✓ Model: {model}[/green]\n")
        return provider_id, model, None

    if provider_id == "cohere":
        console.print("[dim]Reutilizando a Cohere API key do passo 1.[/dim]\n")
        return provider_id, provider_info["default_model"], cohere_key

    console.print(
        f"[bold]{provider_info['name']} API key:[/bold]\n"
        f"[cyan]{provider_info['url']}[/cyan]\n"
    )
    api_key = getpass.getpass(f"{provider_info['name']} API key (oculta): ").strip()
    if not api_key:
        console.print(f"[red]API key é obrigatória para {provider_info['name']}.[/red]")
        sys.exit(1)
    console.print(f"[green]✓ {provider_info['name']} key recebida.[/green]\n")
    return provider_id, provider_info["default_model"], api_key


# ---------------------------------------------------------------------------
# Fluxo principal
# ---------------------------------------------------------------------------


async def run_setup() -> None:
    """Executa o wizard completo de configuração de keys + LLM provider."""
    console = Console()

    console.print(
        Panel(
            "[bold cyan]Vectora — Configuração de API Keys[/bold cyan]\n"
            "[dim]Configure suas API keys e escolha o LLM provider[/dim]",
            style="bold blue",
            expand=False,
        )
    )
    console.print()

    cohere_key = await _step_cohere_key(console)
    tavily_key = await _step_tavily_key(console)
    provider_id, model, llm_api_key = await _step_select_llm(console, cohere_key)

    await _test_connection(console, provider_id, model, llm_api_key)

    keys_to_save: dict[str, str] = {
        "COHERE_API_KEY": cohere_key,
        "TAVILY_API_KEY": tavily_key,
    }
    if llm_api_key and provider_id != "cohere":
        env_var = next(
            (
                v["env_var"]
                for v in LLM_PROVIDERS.values()
                if v["provider_id"] == provider_id
            ),
            "",
        )
        if env_var:
            keys_to_save[env_var] = llm_api_key

    save_keys_to_env(keys_to_save)
    _save_provider_to_settings(provider_id, model)

    console.print(
        Panel(
            "[green]✓ API keys salvas em ~/.vectora/.env, "
            "provider/model em app_settings[/green]",
            title="[bold]Setup Completo[/bold]",
            style="green",
            expand=False,
        )
    )
    console.print()
    console.print(Rule("[bold cyan]Setup concluído[/bold cyan]", style="cyan"))
    console.print()


def run_keys() -> None:
    """Entry point síncrono de ``vectora config keys``."""
    asyncio.run(run_setup())
