"""Casos felizes, bordas e recuperação do DAG persistente."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

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
    with pytest.raises(DAGValidationError):
        validate_stages([DAGStage("a", "A", ("b", "b")), DAGStage("b", "B")])


@pytest.mark.asyncio
async def test_approval_requires_a_non_empty_human_actor(tmp_path) -> None:
    db = await aiosqlite.connect(tmp_path / "dag-approval.db")
    store = DAGStore(db)
    await store.setup()
    plan_id, plan_hash = await store.create_plan(
        run_id="run-approval", stages=[DAGStage("only", "Only")]
    )

    with pytest.raises(DAGValidationError, match="approver"):
        await store.approve_plan(plan_id, plan_hash=plan_hash, approver="  ")

    await db.close()


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
    other_plan, other_hash = await store.create_plan(
        run_id="run-2", stages=[DAGStage("other", "Other")]
    )
    await store.approve_plan(other_plan, plan_hash=other_hash, approver="user-1")
    with pytest.raises(DAGValidationError, match="outro plano"):
        await store.start_execution(
            other_plan, run_id="run-2", idempotency_key="idem-1"
        )
    claimed = await store.claim_ready_stage(execution.execution_id, lease_seconds=1)
    assert claimed is not None
    expired_token = claimed[1]
    await db.execute(
        "UPDATE vectora_dag_stages SET claim_expires_at=? WHERE execution_id=?",
        ("2000-01-01T00:00:00+00:00", execution.execution_id),
    )
    await db.commit()
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
    async with db.execute(
        "SELECT COUNT(*) FROM vectora_dag_events "
        "WHERE execution_id=? AND stage_id=? AND event_type='stage_recovered'",
        (execution.execution_id, claimed[0]),
    ) as cursor:
        recovered_events = await cursor.fetchone()
    assert recovered_events is not None and recovered_events[0] == 1
    with pytest.raises(DAGValidationError):
        await store.finish_stage(
            execution.execution_id,
            claimed[0],
            claim_token=expired_token,
            passed=True,
            output={"stale": True},
        )
    async with db.execute(
        "SELECT status, output_json FROM vectora_dag_stages "
        "WHERE execution_id=? AND stage_id=?",
        (execution.execution_id, claimed[0]),
    ) as cursor:
        stale_row = await cursor.fetchone()
    assert stale_row is not None
    assert stale_row[0] == "running"
    assert stale_row[1] is None
    await store.finish_stage(
        execution.execution_id,
        claimed[0],
        claim_token=claimed[1],
        passed=True,
        output={"ok": True},
    )
    next_stage = await store.claim_ready_stage(execution.execution_id)
    assert next_stage is not None and next_stage[0] == "final"
    await store.finish_stage(
        execution.execution_id,
        "final",
        claim_token=next_stage[1],
        passed=True,
        output={"done": True},
    )
    async with db.execute(
        "SELECT status FROM vectora_dag_executions WHERE execution_id=?",
        (execution.execution_id,),
    ) as cursor:
        execution_row = await cursor.fetchone()
    assert execution_row is not None
    assert execution_row[0] == "passed"
    async with db.execute(
        "SELECT COUNT(*) FROM vectora_dag_events "
        "WHERE execution_id=? AND event_type='execution_finished'",
        (execution.execution_id,),
    ) as cursor:
        finished_events = await cursor.fetchone()
    assert finished_events is not None and finished_events[0] == 1
    assert await store.claim_ready_stage(execution.execution_id) is None
    with pytest.raises(DAGValidationError):
        await store.finish_stage(
            execution.execution_id, "final", claim_token="wrong", passed=True
        )
    await db.close()


@pytest.mark.asyncio
async def test_dag_heartbeat_extends_lease_and_recovery_exhausts_attempts(
    tmp_path,
) -> None:
    db = await aiosqlite.connect(tmp_path / "dag-heartbeat.db")
    db.row_factory = aiosqlite.Row
    store = DAGStore(db)
    await store.setup()
    plan_id, plan_hash = await store.create_plan(
        run_id="run-heartbeat", stages=[DAGStage("only", "Only")]
    )
    await store.approve_plan(plan_id, plan_hash=plan_hash, approver="user-1")
    execution = await store.start_execution(
        plan_id, run_id="run-heartbeat", idempotency_key="heartbeat"
    )
    claimed = await store.claim_ready_stage(execution.execution_id, lease_seconds=60)
    assert claimed is not None
    assert await store.heartbeat_stage(
        execution.execution_id, claimed[0], claim_token=claimed[1], lease_seconds=60
    )
    assert not await store.heartbeat_stage(
        execution.execution_id, claimed[0], claim_token="stale", lease_seconds=60
    )
    await db.execute(
        "UPDATE vectora_dag_stages SET claim_expires_at=? WHERE execution_id=?",
        ("2000-01-01T00:00:00+00:00", execution.execution_id),
    )
    await db.commit()
    assert await store.recover_expired_claims(max_attempts=1) == 1
    async with db.execute(
        "SELECT status FROM vectora_dag_stages WHERE execution_id=?",
        (execution.execution_id,),
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None and row[0] == "failed"
    assert await store.claim_ready_stage(execution.execution_id) is None
    await db.close()


@pytest.mark.asyncio
async def test_dag_nao_finaliza_claim_expirado_sem_recovery(tmp_path) -> None:
    """A conclusão não pode vencer a lease sem passar pelo recovery."""
    db = await aiosqlite.connect(tmp_path / "dag-expiry.db")
    db.row_factory = aiosqlite.Row
    store = DAGStore(db)
    await store.setup()
    plan_id, plan_hash = await store.create_plan(
        run_id="run-expiry", stages=[DAGStage("only", "Only")]
    )
    await store.approve_plan(plan_id, plan_hash=plan_hash, approver="user-1")
    execution = await store.start_execution(
        plan_id, run_id="run-expiry", idempotency_key="expiry"
    )
    claimed = await store.claim_ready_stage(execution.execution_id, lease_seconds=60)
    assert claimed is not None
    await db.execute(
        "UPDATE vectora_dag_stages SET claim_expires_at=? WHERE execution_id=?",
        ("2000-01-01T00:00:00+00:00", execution.execution_id),
    )
    await db.commit()

    with pytest.raises(DAGValidationError, match="claim da etapa expirou"):
        await store.finish_stage(
            execution.execution_id,
            claimed[0],
            claim_token=claimed[1],
            passed=True,
            output={"stale": True},
        )

    async with db.execute(
        "SELECT status, output_json FROM vectora_dag_stages WHERE execution_id=?",
        (execution.execution_id,),
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None and row[0] == "running" and row[1] is None
    await db.close()


@pytest.mark.asyncio
async def test_dag_watchdog_renews_claim_until_executor_finishes(tmp_path) -> None:
    db = await aiosqlite.connect(tmp_path / "dag-watchdog.db")
    db.row_factory = aiosqlite.Row
    store = DAGStore(db)
    await store.setup()
    plan_id, plan_hash = await store.create_plan(
        run_id="run-watchdog", stages=[DAGStage("only", "Only")]
    )
    await store.approve_plan(plan_id, plan_hash=plan_hash, approver="user-1")
    execution = await store.start_execution(
        plan_id, run_id="run-watchdog", idempotency_key="watchdog"
    )
    claimed = await store.claim_ready_stage(execution.execution_id, lease_seconds=1)
    assert claimed is not None
    async with store.stage_lease_watchdog(
        execution.execution_id,
        claimed[0],
        claim_token=claimed[1],
        lease_seconds=1,
        interval_seconds=0.01,
    ):
        await asyncio.sleep(0.03)
        async with db.execute(
            "SELECT claim_expires_at FROM vectora_dag_stages "
            "WHERE execution_id=? AND stage_id=?",
            (execution.execution_id, claimed[0]),
        ) as cursor:
            row = await cursor.fetchone()
        assert row is not None
        assert row[0] > datetime.now(UTC).isoformat()
    await store.finish_stage(
        execution.execution_id,
        claimed[0],
        claim_token=claimed[1],
        passed=True,
        output={"ok": True},
    )
    await db.close()


@pytest.mark.asyncio
async def test_dag_failed_stage_finishes_execution_once(tmp_path) -> None:
    db = await aiosqlite.connect(tmp_path / "dag-failure.db")
    db.row_factory = aiosqlite.Row
    store = DAGStore(db)
    await store.setup()
    plan_id, plan_hash = await store.create_plan(
        run_id="run-failure", stages=[DAGStage("only", "Only")]
    )
    await store.approve_plan(plan_id, plan_hash=plan_hash, approver="user-1")
    execution = await store.start_execution(
        plan_id, run_id="run-failure", idempotency_key="failure"
    )
    claimed = await store.claim_ready_stage(execution.execution_id)
    assert claimed is not None
    await store.finish_stage(
        execution.execution_id,
        claimed[0],
        claim_token=claimed[1],
        passed=False,
        error="boom",
    )
    async with db.execute(
        "SELECT status FROM vectora_dag_executions WHERE execution_id=?",
        (execution.execution_id,),
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None and row[0] == "failed"
    async with db.execute(
        "SELECT COUNT(*) FROM vectora_dag_events "
        "WHERE execution_id=? AND event_type='execution_finished'",
        (execution.execution_id,),
    ) as cursor:
        events = await cursor.fetchone()
    assert events is not None and events[0] == 1
    await db.close()


@pytest.mark.asyncio
async def test_dag_failure_blocks_pending_stages_and_is_terminal(tmp_path) -> None:
    """A failed execution must not leave dependent work claimable forever."""
    db = await aiosqlite.connect(tmp_path / "dag-failure-dependent.db")
    db.row_factory = aiosqlite.Row
    store = DAGStore(db)
    await store.setup()
    plan_id, plan_hash = await store.create_plan(
        run_id="run-failure-dependent",
        stages=[DAGStage("first", "First"), DAGStage("second", "Second", ("first",))],
    )
    await store.approve_plan(plan_id, plan_hash=plan_hash, approver="user-1")
    execution = await store.start_execution(
        plan_id,
        run_id="run-failure-dependent",
        idempotency_key="failure-dependent",
    )
    claimed = await store.claim_ready_stage(execution.execution_id)
    assert claimed is not None
    await store.finish_stage(
        execution.execution_id,
        claimed[0],
        claim_token=claimed[1],
        passed=False,
        error="boom",
    )

    assert await store.claim_ready_stage(execution.execution_id) is None
    async with db.execute(
        "SELECT status,error FROM vectora_dag_stages "
        "WHERE execution_id=? AND stage_id='second'",
        (execution.execution_id,),
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None
    assert row[0] == "failed"
    assert "outra etapa falhou" in row[1]
    async with db.execute(
        "SELECT status FROM vectora_dag_executions WHERE execution_id=?",
        (execution.execution_id,),
    ) as cursor:
        execution_row = await cursor.fetchone()
    assert execution_row is not None and execution_row[0] == "failed"
    await db.close()


@pytest.mark.asyncio
async def test_dag_failure_is_not_erased_when_sibling_finishes_after_it(
    tmp_path,
) -> None:
    """A successful sibling cannot turn a partially failed DAG into success."""
    db = await aiosqlite.connect(tmp_path / "dag-failure-sibling.db")
    db.row_factory = aiosqlite.Row
    store = DAGStore(db)
    await store.setup()
    plan_id, plan_hash = await store.create_plan(
        run_id="run-failure-sibling",
        stages=[DAGStage("first", "First"), DAGStage("second", "Second")],
    )
    await store.approve_plan(plan_id, plan_hash=plan_hash, approver="user-1")
    execution = await store.start_execution(
        plan_id,
        run_id="run-failure-sibling",
        idempotency_key="failure-sibling",
    )
    first = await store.claim_ready_stage(execution.execution_id)
    second = await store.claim_ready_stage(execution.execution_id)
    assert first is not None and second is not None
    await store.finish_stage(
        execution.execution_id,
        first[0],
        claim_token=first[1],
        passed=False,
        error="boom",
    )
    async with db.execute(
        "SELECT status FROM vectora_dag_executions WHERE execution_id=?",
        (execution.execution_id,),
    ) as cursor:
        running_row = await cursor.fetchone()
    assert running_row is not None and running_row[0] == "running"
    await store.finish_stage(
        execution.execution_id,
        second[0],
        claim_token=second[1],
        passed=True,
    )
    async with db.execute(
        "SELECT status FROM vectora_dag_executions WHERE execution_id=?",
        (execution.execution_id,),
    ) as cursor:
        terminal_row = await cursor.fetchone()
    assert terminal_row is not None and terminal_row[0] == "failed"
    await db.close()


@pytest.mark.asyncio
async def test_dag_binds_execution_to_plan_run_and_rejects_reused_key_for_other_run(
    tmp_path,
) -> None:
    """Reject a plan-step execution reused by a different run."""
    db = await aiosqlite.connect(tmp_path / "dag-run-binding.db")
    store = DAGStore(db)
    await store.setup()
    plan_id, plan_hash = await store.create_plan(
        run_id="run-owner", stages=[DAGStage("only", "Only")]
    )
    await store.approve_plan(plan_id, plan_hash=plan_hash, approver="user-1")
    with pytest.raises(DAGValidationError, match="run_id do plano"):
        await store.start_execution(plan_id, run_id="run-other", idempotency_key="k")
    execution = await store.start_execution(
        plan_id, run_id="run-owner", idempotency_key="k"
    )
    with pytest.raises(DAGValidationError, match="run_id do plano"):
        await store.start_execution(plan_id, run_id="run-other", idempotency_key="k")
    assert execution.run_id == "run-owner"
    await db.close()


@pytest.mark.asyncio
async def test_dag_rejects_mutated_approved_plan_before_creating_execution(tmp_path):
    """A plan aprovado não pode ser alterado entre aprovação e execução."""
    db = await aiosqlite.connect(tmp_path / "dag-integrity.db")
    store = DAGStore(db)
    await store.setup()
    plan_id, plan_hash = await store.create_plan(
        run_id="run-integrity", stages=[DAGStage("only", "Only")]
    )
    await store.approve_plan(plan_id, plan_hash=plan_hash, approver="user-1")
    await db.execute(
        "UPDATE vectora_dag_plans SET stages_json=? WHERE plan_id=?",
        ('[{"stage_id":"only","name":"Tampered","depends_on":[]}]', plan_id),
    )
    await db.commit()
    with pytest.raises(DAGValidationError, match="hash do plano"):
        await store.start_execution(
            plan_id, run_id="run-integrity", idempotency_key="integrity-1"
        )
    async with db.execute(
        "SELECT COUNT(*) FROM vectora_dag_executions WHERE plan_id=?", (plan_id,)
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None and row[0] == 0
    await db.close()


@pytest.mark.asyncio
async def test_dag_persiste_estagios_canonicos_e_nao_o_json_cru(tmp_path) -> None:
    """Valores armazenados que coincidem com o estágio canônico (ids numéricos)
    viram texto canônico nas linhas de execução; o plano adulterado de verdade
    continua sendo rejeitado."""
    db = await aiosqlite.connect(tmp_path / "dag-canonical.db")
    store = DAGStore(db)
    await store.setup()
    plan_id, plan_hash = await store.create_plan(
        run_id="run-canonical",
        stages=[DAGStage("1", "Primeiro"), DAGStage("2", "Segundo", ("1",))],
    )
    await store.approve_plan(plan_id, plan_hash=plan_hash, approver="user-1")
    await db.execute(
        "UPDATE vectora_dag_plans SET stages_json=? WHERE plan_id=?",
        (
            (
                '[{"stage_id":1,"name":"Primeiro","depends_on":[]},'
                '{"stage_id":2,"name":"Segundo","depends_on":[1]}]'
            ),
            plan_id,
        ),
    )
    await db.commit()

    execution = await store.start_execution(
        plan_id, run_id="run-canonical", idempotency_key="canonical-1"
    )
    async with db.execute(
        "SELECT stage_id,depends_on_json FROM vectora_dag_stages "
        "WHERE execution_id=? ORDER BY stage_id",
        (execution.execution_id,),
    ) as cursor:
        rows = [(row[0], row[1]) for row in await cursor.fetchall()]
    assert rows == [("1", "[]"), ("2", '["1"]')]
    await db.close()


@pytest.mark.asyncio
async def test_dag_cancel_invalidates_claims_and_is_idempotent(tmp_path) -> None:
    """Cancel a DAG idempotently and invalidate outstanding claims."""
    db = await aiosqlite.connect(tmp_path / "dag-cancel.db")
    store = DAGStore(db)
    await store.setup()
    plan_id, plan_hash = await store.create_plan(
        run_id="run-cancel", stages=[DAGStage("only", "Only")]
    )
    await store.approve_plan(plan_id, plan_hash=plan_hash, approver="user-1")
    execution = await store.start_execution(
        plan_id, run_id="run-cancel", idempotency_key="cancel"
    )
    claimed = await store.claim_ready_stage(execution.execution_id)
    assert claimed is not None
    assert await store.cancel_execution(
        execution.execution_id, requested_by="user-1", reason="user stopped"
    )
    assert not await store.cancel_execution(
        execution.execution_id, requested_by="user-1"
    )
    assert await store.claim_ready_stage(execution.execution_id) is None
    with pytest.raises(DAGValidationError):
        await store.finish_stage(
            execution.execution_id,
            claimed[0],
            claim_token=claimed[1],
            passed=True,
        )
    async with db.execute(
        "SELECT status FROM vectora_dag_executions WHERE execution_id=?",
        (execution.execution_id,),
    ) as cursor:
        row = await cursor.fetchone()
    assert row is not None and row[0] == "cancelled"
    await db.close()
