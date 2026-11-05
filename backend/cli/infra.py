"""``vectora config docker|qdrant|redis`` — infra e backends de dados.

Comandos operacionais para VPS via SSH:
  - ``config docker [up|down|status]`` — sobe/para/consulta a stack local
    (Postgres+pgvector, Redis, Qdrant) reusando ``backend.storage.dev_stack``.
  - ``config qdrant <url> [--api-key]`` — testa a conexão e persiste em
    ``~/.vectora/.env`` (``QDRANT_URL``/``QDRANT_API_KEY`` + ``STORAGE_MODE=complete``).
  - ``config redis <url>`` — testa a conexão e persiste ``REDIS_URL``.
"""

from __future__ import annotations

import sys
from pathlib import Path

from rich.console import Console

from backend.cli.keys import upsert_env_key
from backend.settings import settings


def _env_file() -> Path:
    env_file = settings.vectora_home / ".env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    return env_file


# ---------------------------------------------------------------------------
# docker
# ---------------------------------------------------------------------------


def run_docker(action: str) -> None:
    """``vectora config docker [up|down|status]`` — infra local via Docker."""
    from backend.storage.dev_stack import (
        connection_urls,
        stack_down,
        stack_status,
        stack_up,
    )

    console = Console()
    action = action or "status"

    if action == "up":
        console.print("[bold]Subindo infra local (Postgres, Redis, Qdrant)…[/bold]")
        result = stack_up()
    elif action == "down":
        console.print("[bold]Parando infra local…[/bold]")
        result = stack_down()
    elif action == "status":
        console.print("[bold]Status da infra local:[/bold]")
        result = stack_status()
    else:
        console.print(
            f"[red]Ação desconhecida: {action!r}[/red] — use up | down | status"
        )
        sys.exit(1)

    for msg in result.messages:
        prefix = "[green]✓[/green]" if result.ok else "[yellow]•[/yellow]"
        console.print(f"  {prefix} {msg}")

    if not result.ok:
        console.print("[red]✗ Houve falhas — veja as mensagens acima.[/red]")
        sys.exit(1)

    if action == "up":
        console.print(
            "\n[green]✓ Infra de desenvolvimento no ar.[/green] "
            "As URLs abaixo já são o default do Vectora — nenhuma config extra:"
        )
        for key, value in connection_urls().items():
            console.print(f"  [cyan]{key}[/cyan]={value}")
        console.print(
            "\nPara usar Postgres/Qdrant como storage primário: "
            "[cyan]STORAGE_MODE=complete[/cyan] (Redis é detectado sozinho)."
        )


# ---------------------------------------------------------------------------
# qdrant
# ---------------------------------------------------------------------------


async def _test_qdrant(url: str, api_key: str | None) -> None:
    from qdrant_client import QdrantClient

    def _check() -> None:
        client = QdrantClient(url=url, api_key=api_key or None)
        client.get_collections()

    import asyncio

    await asyncio.to_thread(_check)


def run_qdrant(url: str, api_key: str | None) -> None:
    """``vectora config qdrant <url> [--api-key]`` — testa e persiste."""
    import asyncio

    console = Console()
    if not url:
        console.print(
            "[red]Informe a URL: vectora config qdrant <url> [--api-key][/red]"
        )
        sys.exit(1)

    console.print(f"Testando Qdrant em [cyan]{url}[/cyan]…")
    try:
        asyncio.run(_test_qdrant(url, api_key))
    except Exception as exc:
        console.print(f"[red]✗ Falha ao conectar:[/red] {exc}")
        sys.exit(1)

    env_file = _env_file()
    upsert_env_key(env_file, "QDRANT_URL", url)
    if api_key:
        upsert_env_key(env_file, "QDRANT_API_KEY", api_key)

    from backend.workspace.runtime_settings import runtime_settings

    runtime_settings.storage_mode = "complete"

    console.print(f"[green]✓ Qdrant conectado e salvo em {env_file}[/green]")


# ---------------------------------------------------------------------------
# redis
# ---------------------------------------------------------------------------


async def _test_redis(url: str) -> None:
    from redis import asyncio as aioredis

    client = aioredis.from_url(url)
    try:
        # redis-py tipa ping() como Awaitable[bool] | bool (cliente sync/async
        # no mesmo stub); no cliente async é sempre awaitable.
        await client.ping()
    finally:
        await client.aclose()


def run_redis(url: str) -> None:
    """``vectora config redis <url>`` — testa e persiste."""
    import asyncio

    console = Console()
    if not url:
        console.print("[red]Informe a URL: vectora config redis <url>[/red]")
        sys.exit(1)

    console.print(f"Testando Redis em [cyan]{url}[/cyan]…")
    try:
        asyncio.run(_test_redis(url))
    except Exception as exc:
        console.print(f"[red]✗ Falha ao conectar:[/red] {exc}")
        sys.exit(1)

    env_file = _env_file()
    upsert_env_key(env_file, "REDIS_URL", url)
    console.print(f"[green]✓ Redis conectado e salvo em {env_file}[/green]")
