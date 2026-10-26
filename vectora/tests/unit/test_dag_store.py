"""Casos felizes, bordas e recuperação do DAG persistente."""

from __future__ import annotations

import aiosqlite
import pytest

from backend.scheduling.dag import (
    DAGStage,
    DAGStore,
    DAGValidationError,
    validate_stages,
)


def test_validate_rejects_cycle_and_unknown_dependency() -> None:
    with pytest.raises(DAGValidationError):
        validate_stages([DAGStage("a", "A", ("b",)), DAGStage("b", "B", ("a",))])
    with pytest.raises(DAGValidationError):
        validate_stages([DAGStage("a", "A", ("missing",))])


@pytest.mark.asyncio
async def test_dag_is_idempotent_and_resumes_after_expired_claim(tmp_path) -> None:
    db = await aiosqlite.connect(tmp_path / "dag.db")
    db.row_factory = aiosqlite.Row
    store = DAGStore(db)
    await store.setup()
    draft_id, draft_hash = await store.create_plan(
        run_id="draft-run", stages=[DAGStage("only", "Only")]
    )
    with pytest.raises(DAGValidationError):
        await store.start_execution(
            draft_id, run_id="draft-run", idempotency_key="draft"
        )
    with pytest.raises(DAGValidationError):
        await store.approve_plan(draft_id, plan_hash="wrong", approver="user-1")
    await store.approve_plan(draft_id, plan_hash=draft_hash, approver="user-1")
    with pytest.raises(DAGValidationError):
        await store.approve_plan(draft_id, plan_hash=draft_hash, approver="user-2")
    plan_id, plan_hash = await store.create_plan(
        run_id="run-1",
        stages=[
            DAGStage("planner", "Planner"),
            DAGStage("final", "Final", ("planner",)),
        ],
    )
    await store.approve_plan(plan_id, plan_hash=plan_hash, approver="user-1")
    execution = await store.start_execution(
        plan_id, run_id="run-1", idempotency_key="idem-1"
    )
    same = await store.start_execution(
        plan_id, run_id="run-1", idempotency_key="idem-1"
    )
    assert same.execution_id == execution.execution_id
    claimed = await store.claim_ready_stage(execution.execution_id, lease_seconds=-1)
    assert claimed is not None
    assert await store.recover_expired_claims() == 1
    claimed = await store.claim_ready_stage(execution.execution_id)
    assert claimed is not None
    async with db.execute(
        "SELECT payload_json FROM vectora_dag_events "
        "WHERE execution_id=? AND stage_id=? AND event_type='stage_claimed' "
        "ORDER BY created_at DESC LIMIT 1",
        (execution.execution_id, claimed[0]),
    ) as cursor:
        event = await cursor.fetchone()
    assert event is not None
    assert '"attempt": 2' in event["payload_json"]
    await store.finish_stage(
        execution.execution_id,
        claimed[0],
        claim_token=claimed[1],
        passed=True,
        output={"ok": True},
    )
    next_stage = await store.claim_ready_stage(execution.execution_id)
    assert next_stage is not None and next_stage[0] == "final"
    with pytest.raises(DAGValidationError):
        await store.finish_stage(
            execution.execution_id, "final", claim_token="wrong", passed=True
        )
    await db.close()
