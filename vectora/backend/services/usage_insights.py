"""Persistência e agregação de métricas técnicas de uso.

Os eventos armazenam somente metadados necessários ao insight semanal: nunca
recebem texto, argumentos, resultados de tools, anexos ou URLs de conteúdo.
"""

from __future__ import annotations

import contextlib
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any


class UsageInsightStore:
    """Armazena eventos técnicos e devolve apenas agregados por usuário."""

    @staticmethod
    def window_bounds(
        weeks: int, *, now: datetime | None = None
    ) -> tuple[datetime, datetime]:
        """Return stable UTC week boundaries for the requested insight window."""
        if weeks not in {1, 2, 4}:
            raise ValueError("weeks deve ser 1, 2 ou 4")
        current = now or datetime.now(UTC)
        week_start = (current - timedelta(days=current.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = week_start + timedelta(days=7)
        return end - timedelta(days=7 * weeks), end

    async def ensure_schema(self, db: Any) -> None:
        if hasattr(db, "acquire"):
            return
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_insight_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                model TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                total_tokens INTEGER,
                estimated_cost_cents REAL,
                tool_names TEXT NOT NULL DEFAULT '[]'
            )
            """
        )
        columns = await db.execute_fetchall("PRAGMA table_info(usage_insight_events)")
        if not any(str(column[1]) == "event_id" for column in columns):
            await db.execute(
                "ALTER TABLE usage_insight_events ADD COLUMN event_id TEXT NOT NULL DEFAULT ''"
            )
        await db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_usage_insight_event_id "
            "ON usage_insight_events(user_id, event_id) WHERE event_id <> ''"
        )
        await db.execute(
            "CREATE INDEX IF NOT EXISTS idx_usage_insight_user_time "
            "ON usage_insight_events(user_id, occurred_at)"
        )
        await db.commit()

    async def record(
        self,
        db: Any,
        *,
        user_id: str,
        event_id: str,
        model: str | None,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
        estimated_cost_cents: float | None,
        tool_names: list[str],
        occurred_at: datetime | None = None,
    ) -> None:
        """Registra um evento já normalizado, sem dados de conteúdo."""
        import json

        when = (occurred_at or datetime.now(UTC)).isoformat()
        if hasattr(db, "acquire"):
            async with db.acquire() as connection:
                await connection.execute(
                    """INSERT INTO usage_insight_events
                    (user_id, event_id, occurred_at, model, input_tokens,
                     output_tokens, total_tokens, estimated_cost_cents, tool_names)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                    ON CONFLICT (user_id, event_id) DO NOTHING""",
                    user_id,
                    event_id,
                    when,
                    model,
                    input_tokens,
                    output_tokens,
                    total_tokens,
                    estimated_cost_cents,
                    json.dumps(sorted(set(tool_names)), ensure_ascii=False),
                )
            return
        await db.execute(
            """INSERT OR IGNORE INTO usage_insight_events
            (user_id, event_id, occurred_at, model, input_tokens, output_tokens,
             total_tokens, estimated_cost_cents, tool_names)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
                event_id,
                when,
                model,
                input_tokens,
                output_tokens,
                total_tokens,
                estimated_cost_cents,
                json.dumps(sorted(set(tool_names)), ensure_ascii=False),
            ),
        )
        await db.commit()

    async def aggregate(
        self,
        db: Any,
        *,
        user_id: str,
        weeks: int = 1,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Agrega a janela móvel permitida sem retornar eventos brutos."""
        if weeks not in {1, 2, 4}:
            raise ValueError("weeks deve ser 1, 2 ou 4")
        start, end = self.window_bounds(weeks, now=now)
        if hasattr(db, "acquire"):
            async with db.acquire() as connection:
                rows = await connection.fetch(
                    """SELECT event_id, model, input_tokens, output_tokens, total_tokens,
                              estimated_cost_cents, tool_names
                       FROM usage_insight_events
                       WHERE user_id = $1 AND occurred_at >= $2 AND occurred_at < $3""",
                    user_id,
                    start,
                    end,
                )
            return self._aggregate_rows(rows, weeks, start=start, end=end)
        async with db.execute(
            """SELECT event_id, model, input_tokens, output_tokens, total_tokens,
                      estimated_cost_cents, tool_names
               FROM usage_insight_events
               WHERE user_id = ? AND occurred_at >= ? AND occurred_at < ?""",
            (user_id, start.isoformat(), end.isoformat()),
        ) as cursor:
            rows = await cursor.fetchall()

        return self._aggregate_rows(rows, weeks, start=start, end=end)

    def _aggregate_rows(
        self, rows: list[Any], weeks: int, *, start: datetime, end: datetime
    ) -> dict[str, Any]:
        """Agrega linhas SQLite ou asyncpg com o mesmo contrato público."""
        import json

        model_counts: Counter[str] = Counter()
        tool_counts: Counter[str] = Counter()
        input_total = output_total = total_total = 0
        known_cost = 0.0
        unknown_cost_events = 0
        turn_ids: set[str] = set()
        counted_tools_turns: set[str] = set()
        for row in rows:
            if hasattr(row, "get"):
                event_id, model, inp, out, total, cost, names = (
                    row.get("event_id"),
                    row.get("model"),
                    row.get("input_tokens"),
                    row.get("output_tokens"),
                    row.get("total_tokens"),
                    row.get("estimated_cost_cents"),
                    row.get("tool_names"),
                )
            elif len(row) == 7:
                event_id, model, inp, out, total, cost, names = row
            else:
                event_id = ""
                model, inp, out, total, cost, names = row
            event_key = str(event_id or "")
            turn_key = (
                event_key.rsplit(":call:", 1)[0] if ":call:" in event_key else event_key
            )
            if turn_key:
                turn_ids.add(turn_key)
            if model:
                model_counts[str(model)] += 1
            input_total += int(inp or 0)
            output_total += int(out or 0)
            total_total += int(total or 0)
            if cost is None:
                unknown_cost_events += 1
            else:
                known_cost += float(cost)
            if turn_key not in counted_tools_turns:
                with contextlib.suppress(TypeError, ValueError):
                    tool_counts.update(str(name) for name in json.loads(names or "[]"))
                counted_tools_turns.add(turn_key)
        return {
            "window_weeks": weeks,
            "window_start": start.isoformat(),
            "window_end": end.isoformat(),
            "event_count": len(turn_ids) if turn_ids else len(rows),
            "input_tokens": input_total,
            "output_tokens": output_total,
            "total_tokens": total_total,
            "estimated_cost_cents": round(known_cost, 4)
            if rows and unknown_cost_events < len(rows)
            else None,
            "unknown_cost_events": unknown_cost_events,
            "most_used_model": min(
                model_counts, key=lambda name: (-model_counts[name], name)
            )
            if model_counts
            else None,
            "tools": [
                {"name": name, "count": count}
                for name, count in sorted(
                    tool_counts.items(), key=lambda item: (-item[1], item[0])
                )
            ],
        }


usage_insight_store = UsageInsightStore()


async def get_usage_database() -> Any:
    """Retorna o backend ativo: SQLite no modo lite e pool Postgres no complete."""
    from backend.services.license import get_effective_storage_mode

    if get_effective_storage_mode() == "complete":
        from backend.storage.factory import get_pg_pool

        return await get_pg_pool()
    from backend.api.handlers.threads import _get_db

    return await _get_db()
