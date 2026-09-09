"""``vectora storage`` — migrations, diagnóstico, backup/restore e wizard BaaS."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rich.console import Console


async def _run_storage_async(args: argparse.Namespace) -> None:
    """Dispatcher de subcomandos ``vectora storage *`` (F11)."""
    from rich.console import Console

    console = Console()

    db_path: str | None = getattr(args, "db", None)
    if not db_path:
        try:
            from backend.settings import settings as _s

            db_path = _s.db_dsn
        except Exception:
            pass
    if not db_path:
        from backend.settings import settings as _s2

        db_path = str(_s2.vectora_home / "data" / "vectora.db")

    action = getattr(args, "action", "info") or "info"
    subaction = getattr(args, "subaction", None)
    version = getattr(args, "version", None)

    try:
        if action == "info":
            await _storage_info(console)
        elif action in ("up", "down"):
            _storage_stack(console, action)
        elif action == "test":
            dsn = subaction or ""
            if not dsn:
                console.print("[red]❌ Informe o DSN: vectora storage test <DSN>[/red]")
                sys.exit(1)
            await _storage_test(console, dsn)
        elif action == "wizard":
            await _storage_wizard(console)
        elif action == "backup":
            output = getattr(args, "output", None)
            await _storage_backup(console, db_path, output)
        elif action == "restore":
            archive = subaction or ""
            if not archive:
                console.print(
                    "[red]❌ Informe o arquivo: vectora storage restore <arquivo>[/red]"
                )
                sys.exit(1)
            await _storage_restore(console, archive, db_path)
        elif action == "migrate":
            await _storage_migrate(console, db_path, subaction or "status", version)
        else:
            console.print(f"[red]Ação desconhecida: {action!r}[/red]")
            sys.exit(1)
    except Exception as exc:
        console.print(f"[red]❌ Erro:[/red] {exc}")
        sys.exit(1)


def _storage_stack(console: Console, action: str) -> None:
    """``vectora storage up|down`` — infra local (Postgres, Redis, Qdrant)."""
    from backend.storage.dev_stack import connection_urls, stack_down, stack_up

    if action == "up":
        console.print("[bold]Subindo infra local (Postgres, Redis, Qdrant)…[/bold]")
        result = stack_up()
    else:
        console.print("[bold]Parando infra local…[/bold]")
        result = stack_down()

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


async def _storage_info(console: Console) -> None:
    """``vectora storage info`` — status de todos os backends."""
    from rich.table import Table

    from backend.storage.factory import storage_health

    console.print("[bold]Storage Health Check[/bold]")
    health = await storage_health()
    table = Table(show_lines=False)
    table.add_column("Backend", style="cyan", width=22)
    table.add_column("Status", width=10)
    table.add_column("Detalhe", style="dim")

    for key, val in health.items():
        if val.get("ok") is True:
            status = "[green]✓ ok[/green]"
            detail = ""
            if "tables" in val:
                detail = f"{len(val['tables'])} tabelas"
        elif val.get("ok") is False:
            status = "[red]✗ erro[/red]"
            detail = str(val.get("error", ""))[:60]
        else:
            status = "[dim]n/a[/dim]"
            detail = str(val.get("error", ""))[:60]
        table.add_row(key, status, detail)
    console.print(table)


async def _storage_test(console: Console, dsn: str) -> None:
    """``vectora storage test <DSN>`` — smoke test de conectividade."""
    import time

    console.print(f"Testando [cyan]{dsn[:40]}…[/cyan]")
    t0 = time.monotonic()
    try:
        if dsn.startswith("postgresql"):
            import asyncpg

            normalized = dsn.replace("postgresql+asyncpg://", "postgresql://")
            conn = await asyncpg.connect(normalized)
            await conn.execute("SELECT 1")
            await conn.close()
        elif dsn.startswith(("https://", "http://")):
            from qdrant_client import QdrantClient

            client = QdrantClient(url=dsn)
            client.get_collections()
        else:
            import aiosqlite

            async with aiosqlite.connect(dsn) as conn:
                await conn.execute("SELECT 1")

        ms = round((time.monotonic() - t0) * 1000, 1)
        console.print(f"[green]✓ Conexão OK[/green] ({ms}ms)")
    except Exception as exc:
        console.print(f"[red]✗ Falha:[/red] {exc}")
        sys.exit(1)


async def _storage_wizard(console: Console) -> None:
    """``vectora storage wizard`` — configuração interativa de backend BaaS."""
    console.print("[bold]Vectora Storage Wizard[/bold]")
    console.print(
        "Provedores disponíveis:\n"
        "  [cyan]1[/cyan] Supabase  (Postgres + pgvector gerenciado)\n"
        "  [cyan]2[/cyan] Neon      (Postgres serverless)\n"
        "  [cyan]3[/cyan] Qdrant Cloud (VectorStore gerenciado)\n"
        "  [cyan]4[/cyan] Self-hosted Postgres\n"
        "  [cyan]0[/cyan] Cancelar\n"
    )
    choice = input("Selecione [0-4]: ").strip()

    if choice == "0":
        console.print("[dim]Cancelado.[/dim]")
        return

    if choice == "1":
        host = input("Hostname Supabase (ex: db.xxxx.supabase.co): ").strip()
        password = input("Senha Postgres: ").strip()
        from backend.storage.recipes.supabase import build_dsn

        dsn = build_dsn(host=host, password=password, pooler=True)
        console.print(f"DSN gerado: [cyan]{dsn[:50]}…[/cyan]")
        await _storage_test(console, dsn)
        _save_dsn_to_settings(dsn)

    elif choice == "2":
        host = input("Hostname Neon (ex: ep-xxx.us-east-2.aws.neon.tech): ").strip()
        user = input("Usuário Postgres: ").strip()
        password = input("Senha Postgres: ").strip()
        database = input("Banco [neondb]: ").strip() or "neondb"
        from backend.storage.recipes.neon import build_dsn

        dsn = build_dsn(host=host, user=user, password=password, database=database)
        console.print(f"DSN gerado: [cyan]{dsn[:50]}…[/cyan]")
        await _storage_test(console, dsn)
        _save_dsn_to_settings(dsn)

    elif choice == "3":
        url = input("URL Qdrant Cloud (ex: https://xxx.cloud.qdrant.io): ").strip()
        api_key = input("API Key: ").strip()
        from backend.storage.recipes.qdrant_cloud import healthcheck

        result = await healthcheck(url=url, api_key=api_key)
        if result["ok"]:
            console.print(
                f"[green]✓ Qdrant conectado[/green] — "
                f"{len(result.get('collections', []))} collections"
            )
            _save_qdrant_to_settings(url, api_key)
        else:
            console.print(f"[red]✗ Falha:[/red] {result.get('error')}")
            sys.exit(1)

    elif choice == "4":
        dsn = input("DSN Postgres (postgresql://...): ").strip()
        await _storage_test(console, dsn)
        _save_dsn_to_settings(dsn)

    else:
        console.print("[red]Opção inválida.[/red]")
        sys.exit(1)


def _save_dsn_to_settings(dsn: str) -> None:
    """Persiste postgres_dsn e storage_mode=complete nas settings."""
    try:
        from backend.settings import settings as _s

        _s.postgres_dsn = dsn
        _s.storage_mode = "complete"  # type: ignore[assignment]
    except Exception:
        pass


def _save_qdrant_to_settings(url: str, api_key: str) -> None:
    """Persiste qdrant_url, qdrant_api_key e storage_mode=complete nas settings."""
    try:
        from backend.settings import settings as _s

        _s.qdrant_url = url
        _s.qdrant_api_key = api_key
        _s.storage_mode = "complete"  # type: ignore[assignment]
    except Exception:
        pass


async def _storage_backup(console: Console, db_path: str, output: str | None) -> None:
    """``vectora storage backup`` — exporta um arquivo manifestado."""
    from datetime import UTC, datetime
    from pathlib import Path as _Path

    src = _Path(db_path)
    if not src.is_file():
        console.print(f"[red]Banco não encontrado: {db_path}[/red]")
        sys.exit(1)

    if not output:
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
        output = str(src.with_suffix(f".backup.{ts}.vbackup.zip"))
    from backend.storage.backup_manifest import create_backup

    preview = create_backup(src, output)
    size_mb = preview.size_bytes / 1024 / 1024
    console.print(f"[green]✓ Backup criado:[/green] {output} ({size_mb:.2f} MiB)")


async def _storage_restore(console: Console, archive: str, db_path: str) -> None:
    """``vectora storage restore <arquivo>`` — restaura backup manifestado."""
    from pathlib import Path as _Path

    arc = _Path(archive)
    if not arc.is_file():
        console.print(f"[red]Arquivo não encontrado: {archive}[/red]")
        sys.exit(1)

    from backend.storage.backup_manifest import restore_backup

    restore_backup(arc, _Path(db_path))

    console.print(f"[green]✓ Restaurado:[/green] {db_path}")


async def _storage_migrate(
    console: Console,
    db_path: str,
    subaction: str,
    version: str | None,
) -> None:
    """``vectora storage migrate`` — schema versioning e migração de dados."""
    import aiosqlite
    from rich.table import Table

    from backend.storage.migrations.runner import MigrationRunner

    if subaction in ("to-postgres", "to-qdrant", "to-pgvector", "memory-to-native"):
        await _storage_data_migrate(console, db_path, subaction, version)
        return

    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        runner = MigrationRunner(conn)

        if subaction == "status":
            status = await runner.status()
            table = Table(title="Schema (arquivo único)", show_lines=False)
            table.add_column("Estado", width=12)
            table.add_column("Checksum", style="dim", width=16)
            table.add_column("Aplicado em", style="dim", width=22)
            if not status.applied:
                state = "[yellow]pendente[/yellow]"
                ts = "—"
            elif status.drift:
                state = "[red]drift![/red]"
                ts = (status.applied_at or "")[:19].replace("T", " ")
            else:
                state = "[green]ok[/green]"
                ts = (status.applied_at or "")[:19].replace("T", " ")
            table.add_row(state, status.checksum[:12], ts)
            console.print(table)

        elif subaction == "history":
            from rich.table import Table

            table = Table(title="Histórico de migrations")
            table.add_column("Checksum", style="dim")
            table.add_column("Aplicado em")
            for item in await runner.history():
                table.add_row(item["checksum"][:16], item["applied_at"])
            console.print(table)

        elif subaction == "plan":
            plan = await runner.plan()
            if not plan["applied"]:
                state = "pendente"
            elif plan["drift"]:
                state = "drift"
            else:
                state = "atualizado"
            console.print(
                f"[cyan]Plano de migration:[/cyan] {state}; "
                f"{plan['statement_count']} statements; "
                f"checksum {str(plan['checksum'])[:16]}"
            )

        elif subaction == "upgrade":
            applied = await runner.upgrade()
            if applied:
                console.print("[green]✓ Schema aplicado (checksum mudou).[/green]")
            else:
                console.print("[green]✓ Banco já atualizado — nada a fazer.[/green]")

        else:
            console.print(
                f"[red]Sub-ação desconhecida: {subaction!r}[/red]\n"
                "Opções: status | history | plan | upgrade | "
                "to-postgres | to-qdrant | to-pgvector | memory-to-native"
            )
            sys.exit(1)


async def _storage_data_migrate(
    console: Console,
    db_path: str,
    subaction: str,
    extra: str | None,
) -> None:
    """Migrações de dados (to-postgres, to-qdrant, to-pgvector, memory-to-native)."""
    from backend.storage.migrations.data_migration import (
        migrate_memory_to_native_store,
        migrate_to_pgvector,
        migrate_to_postgres,
        migrate_to_qdrant,
    )

    _s: Any = None
    with contextlib.suppress(Exception):
        from backend.settings import settings as _s

    dry_run = False

    if subaction == "to-postgres":
        postgres_dsn = str((_s and _s.postgres_dsn) or "")
        if not postgres_dsn:
            console.print(
                "[red]❌ postgres_dsn não configurado. "
                "Use POSTGRES_DSN ou vectora storage wizard.[/red]"
            )
            sys.exit(1)
        console.print(f"[bold]Migrando SQLite → Postgres[/bold] (dry_run={dry_run})")
        result = await migrate_to_postgres(
            sqlite_path=db_path,
            postgres_dsn=postgres_dsn,
            dry_run=dry_run,
            console=console,
        )
        console.print(f"[green]Total:[/green] {result['total_rows']} registros")

    elif subaction == "to-qdrant":
        qdrant_url = str((_s and _s.qdrant_url) or "")
        qdrant_api_key_raw = _s and getattr(_s, "qdrant_api_key", None)
        qdrant_api_key: str | None = (
            str(qdrant_api_key_raw) if qdrant_api_key_raw else None
        )
        if not qdrant_url:
            console.print(
                "[red]❌ qdrant_url não configurado. "
                "Use QDRANT_URL ou vectora storage wizard.[/red]"
            )
            sys.exit(1)
        lancedb_path = (
            db_path.replace(".db", "_lancedb") if db_path.endswith(".db") else db_path
        )
        collection = extra or "articles"
        console.print(
            f"[bold]Migrando LanceDB → Qdrant[/bold] "
            f"collection={collection} (dry_run={dry_run})"
        )
        result = await migrate_to_qdrant(
            lancedb_path=lancedb_path,
            qdrant_url=qdrant_url,
            qdrant_api_key=qdrant_api_key,
            collection=collection,
            dry_run=dry_run,
            console=console,
        )
        console.print(f"[green]Total:[/green] {result['upserted']} vetores")

    elif subaction == "to-pgvector":
        postgres_dsn = str((_s and _s.postgres_dsn) or "")
        if not postgres_dsn:
            console.print(
                "[red]❌ postgres_dsn não configurado. "
                "Use POSTGRES_DSN ou vectora storage wizard.[/red]"
            )
            sys.exit(1)
        lancedb_path = (
            db_path.replace(".db", "_lancedb") if db_path.endswith(".db") else db_path
        )
        collection = extra or "articles"
        console.print(
            f"[bold]Migrando LanceDB → pgvector[/bold] "
            f"collection={collection} (dry_run={dry_run})"
        )
        result = await migrate_to_pgvector(
            lancedb_path=lancedb_path,
            postgres_dsn=postgres_dsn,
            collection=collection,
            dry_run=dry_run,
            console=console,
        )
        console.print(f"[green]Total:[/green] {result['upserted']} vetores")

    elif subaction == "memory-to-native":
        console.print(
            f"[bold]Migrando memórias → store nativo[/bold] (dry_run={dry_run})"
        )
        result = await migrate_memory_to_native_store(
            sqlite_path=db_path,
            dry_run=dry_run,
            console=console,
        )
        console.print(f"[green]Total:[/green] {result['migrated']} memórias migradas")


def run_storage(args: argparse.Namespace) -> None:
    """Entry point síncrono de ``vectora storage``."""
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run_storage_async(args))
