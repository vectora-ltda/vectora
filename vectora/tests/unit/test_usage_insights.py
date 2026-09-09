from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest

from backend.scheduling.budget import estimate_cost_cents
from backend.services.usage_insights import UsageInsightStore


@pytest.mark.asyncio
async def test_aggregate_isolated_and_deterministic() -> None:
    store = UsageInsightStore()
    async with aiosqlite.connect(":memory:") as db:
        await store.ensure_schema(db)
        now = datetime(2026, 1, 8, tzinfo=UTC)
        await store.record(
            db,
            user_id="u1",
            event_id="turn-1",
            model="model-b",
            input_tokens=2,
            output_tokens=3,
            total_tokens=5,
            estimated_cost_cents=None,
            tool_names=["z", "a"],
            occurred_at=now,
        )
        await store.record(
            db,
            user_id="u2",
            event_id="turn-2",
            model="private",
            input_tokens=99,
            output_tokens=99,
            total_tokens=198,
            estimated_cost_cents=1,
            tool_names=["secret"],
            occurred_at=now,
        )
        result = await store.aggregate(db, user_id="u1", now=now + timedelta(hours=1))
    assert result["total_tokens"] == 5
    assert result["estimated_cost_cents"] is None
    assert result["tools"] == [{"name": "a", "count": 1}, {"name": "z", "count": 1}]


@pytest.mark.asyncio
async def test_record_is_idempotent_for_same_event() -> None:
    store = UsageInsightStore()
    async with aiosqlite.connect(":memory:") as db:
        await store.ensure_schema(db)
        for _ in range(2):
            await store.record(
                db,
                user_id="u1",
                event_id="turn-1",
                model="openai:gpt-4o-mini",
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                estimated_cost_cents=0.00045,
                tool_names=["file_read"],
            )
        result = await store.aggregate(db, user_id="u1")
    assert result["event_count"] == 1
    assert result["total_tokens"] == 15


def test_window_bounds_are_stable_within_the_same_week() -> None:
    store = UsageInsightStore()
    first = datetime(2026, 1, 8, 12, tzinfo=UTC)
    second = first + timedelta(days=2)

    assert store.window_bounds(1, now=first) == store.window_bounds(1, now=second)

    start, end = store.window_bounds(2, now=first)
    assert end.isoformat() == "2026-01-12T00:00:00+00:00"
    assert start.isoformat() == "2025-12-29T00:00:00+00:00"


def test_estimate_cost_preserves_known_and_unknown_models() -> None:
    assert (
        estimate_cost_cents(
            "openai:gpt-4o-mini", {"input_tokens": 1_000_000, "output_tokens": 0}
        )
        == 15.0
    )
    assert (
        estimate_cost_cents(
            "provider:unpriced", {"input_tokens": 10, "output_tokens": 5}
        )
        is None
    )


@pytest.mark.asyncio
async def test_agrega_multiplos_modelos_do_mesmo_turno_uma_so_vez() -> None:
    store = UsageInsightStore()
    async with aiosqlite.connect(":memory:") as db:
        await store.ensure_schema(db)
        now = datetime(2026, 1, 8, tzinfo=UTC)
        for index, model in enumerate(("openai:gpt-5", "anthropic:claude")):
            await store.record(
                db,
                user_id="u1",
                event_id=f"turn-1:call:{index}",
                model=model,
                input_tokens=10,
                output_tokens=5,
                total_tokens=15,
                estimated_cost_cents=1.0,
                tool_names=["file_read"] if index == 0 else [],
                occurred_at=now,
            )
        result = await store.aggregate(db, user_id="u1", now=now + timedelta(hours=1))
    assert result["event_count"] == 1
    assert result["tools"] == [{"name": "file_read", "count": 1}]
    assert result["total_tokens"] == 30
    assert result["most_used_model"] == "anthropic:claude"
