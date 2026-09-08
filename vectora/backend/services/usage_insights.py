"""Persistência e agregação de métricas técnicas de uso.

Os eventos armazenam somente metadados necessários ao insight semanal: nunca
recebem texto, argumentos, resultados de tools, anexos ou URLs de conteúdo.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any


class UsageInsightStore:
    """Armazena eventos técnicos e devolve apenas agregados por usuário."""

    async def ensure_schema(self, db: Any) -> None:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS usage_insight_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        await db.execute(
            """INSERT INTO usage_insight_events
            (user_id, occurred_at, model, input_tokens, output_tokens,
             total_tokens, estimated_cost_cents, tool_names)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                user_id,
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
        import json

        if weeks not in {1, 2, 4}:
            raise ValueError("weeks deve ser 1, 2 ou 4")
        end = now or datetime.now(UTC)
        start = end - timedelta(days=7 * weeks)
        async with db.execute(
            """SELECT model, input_tokens, output_tokens, total_tokens,
                      estimated_cost_cents, tool_names
               FROM usage_insight_events
               WHERE user_id = ? AND occurred_at >= ? AND occurred_at < ?""",
            (user_id, start.isoformat(), end.isoformat()),
        ) as cursor:
            rows = await cursor.fetchall()

        model_counts: Counter[str] = Counter()
        tool_counts: Counter[str] = Counter()
        input_total = output_total = total_total = 0
        known_cost = 0.0
        unknown_cost_events = 0
        for model, inp, out, total, cost, names in rows:
            if model:
                model_counts[str(model)] += 1
            input_total += int(inp or 0)
            output_total += int(out or 0)
            total_total += int(total or 0)
            if cost is None:
                unknown_cost_events += 1
            else:
                known_cost += float(cost)
            try:
                tool_counts.update(str(name) for name in json.loads(names or "[]"))
            except (TypeError, ValueError):
                continue
        return {
            "window_weeks": weeks,
            "event_count": len(rows),
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
