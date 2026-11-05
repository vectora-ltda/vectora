"""``vectora sessions`` — lista todas as sessões salvas em tabela Rich."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path


async def _run_sessions_async() -> None:
    """Implementa ``vectora sessions``."""
    from rich.console import Console
    from rich.table import Table

    from backend.settings import Settings
    from backend.workspace.runtime_settings import runtime_settings

    try:
        settings = Settings()
        # Threads são armazenados em checkpoints.db (usado pelo threads API handler).
        checkpoints_db = settings.vectora_home / "checkpoints.db"
        if not checkpoints_db.exists():
            sessions = []
        else:
            import aiosqlite

            async with aiosqlite.connect(str(checkpoints_db)) as db:
                try:
                    cursor = await db.execute(
                        "SELECT thread_id, user_type, created_at, last_activity, "
                        "message_count, extra FROM vectora_sessions "
                        "ORDER BY last_activity DESC LIMIT 200"
                    )
                    rows = await cursor.fetchall()
                except Exception:
                    rows = []
            sessions = []
            for row in rows:
                (
                    thread_id,
                    user_type,
                    created_at,
                    last_activity,
                    message_count,
                    extra_raw,
                ) = row
                try:
                    import json as _json

                    extra = _json.loads(extra_raw or "{}")
                except Exception:
                    extra = {}
                sessions.append(
                    {
                        "thread_id": thread_id,
                        "user_type": user_type,
                        "created_at": created_at,
                        "last_activity": last_activity,
                        "message_count": message_count,
                        **extra,
                    }
                )
    except Exception as e:
        print(f"❌ Error listing sessions: {e}")
        sys.exit(1)

    console = Console()

    if not sessions:
        console.print("[dim]No sessions found. Start vectora to create one.[/dim]")
        return

    active_ids = set(runtime_settings.last_session_by_dir.values())

    table = Table(
        title=f"Vectora Sessions ({len(sessions)} total)",
        show_lines=False,
        expand=False,
    )
    table.add_column("ID", style="cyan bold", width=8)
    table.add_column("Created", style="dim", width=20)
    table.add_column("Messages", justify="right", width=9)
    table.add_column("Directory", style="dim")

    for s in sorted(sessions, key=lambda x: str(x.get("created_at", "")), reverse=True):
        tid = str(s.get("thread_id", "?"))
        created = str(s.get("created_at", ""))[:19].replace("T", " ")
        msgs = str(s.get("message_count", 0))
        work_dir_raw = str(s.get("working_directory") or "—")
        work_dir = work_dir_raw
        with contextlib.suppress(ValueError):
            work_dir = f"~/{Path(work_dir_raw).relative_to(Path.home())}"

        marker = " [bold green]◀ active[/bold green]" if tid in active_ids else ""
        table.add_row(tid, created, msgs, f"{work_dir}{marker}")

    console.print(table)


def run_sessions() -> None:
    """Entry point síncrono de ``vectora sessions``."""
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run_sessions_async())
