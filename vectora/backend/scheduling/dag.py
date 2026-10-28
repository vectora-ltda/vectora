"""Persistência mínima e durável para planos executáveis de subagentes.

O Kanban continua sendo a visão operacional das tarefas. Este módulo guarda a
identidade do plano aprovado e o estado de cada etapa, permitindo retomar uma
execução depois de reinício sem executar duas vezes uma etapa já concluída.
Todas as mutações usam CAS (claim token) e deixam um evento auditável.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite


class DAGValidationError(ValueError):
    """Plano malformado ou com ciclo de dependências."""


@dataclass(frozen=True)
class DAGStage:
    stage_id: str
    name: str
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class DAGExecution:
    execution_id: str
    plan_id: str
    run_id: str
    idempotency_key: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _hash_plan(stages: Sequence[DAGStage]) -> str:
    payload = [
        {"stage_id": s.stage_id, "name": s.name, "depends_on": list(s.depends_on)}
        for s in stages
    ]
    return hashlib.sha256(
        json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode()
    ).hexdigest()


def validate_stages(stages: Sequence[DAGStage]) -> None:
    """Rejeita IDs duplicados, dependências desconhecidas e ciclos."""
    ids = [s.stage_id for s in stages]
    if not ids or any(not i.strip() for i in ids) or len(set(ids)) != len(ids):
        raise DAGValidationError("estágios devem ter IDs únicos e não vazios")
    if any(len(s.depends_on) != len(set(s.depends_on)) for s in stages):
        raise DAGValidationError("dependências de estágio não podem ser duplicadas")
    known = set(ids)
    graph = {s.stage_id: set(s.depends_on) for s in stages}
    if any(dep not in known for deps in graph.values() for dep in deps):
        raise DAGValidationError("dependência de estágio inexistente")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise DAGValidationError("ciclo de dependências no plano")
        if node in visited:
            return
        visiting.add(node)
        for dep in graph[node]:
            visit(dep)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)


class DAGStore:
    """Store SQLite compartilhável com o banco de background tasks."""

    def __init__(self, db: aiosqlite.Connection) -> None:
        self.db = db
        self.db.row_factory = aiosqlite.Row

    async def setup(self) -> None:
        await self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS vectora_dag_plans (
                plan_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                workspace_id TEXT, stages_json TEXT NOT NULL,
                plan_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'draft',
                approved_by TEXT, approved_at TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS vectora_dag_executions (
                execution_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL,
                run_id TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'running', created_at TEXT NOT NULL,
                FOREIGN KEY(plan_id) REFERENCES vectora_dag_plans(plan_id)
            );
            CREATE TABLE IF NOT EXISTS vectora_dag_stages (
                execution_id TEXT NOT NULL, stage_id TEXT NOT NULL,
                name TEXT NOT NULL, depends_on_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', attempt INTEGER NOT NULL DEFAULT 0,
                claim_token TEXT, claim_expires_at TEXT, output_json TEXT,
                error TEXT, PRIMARY KEY(execution_id, stage_id),
                FOREIGN KEY(execution_id) REFERENCES vectora_dag_executions(execution_id)
            );
            CREATE TABLE IF NOT EXISTS vectora_dag_events (
                event_id TEXT PRIMARY KEY, execution_id TEXT NOT NULL,
                stage_id TEXT, event_type TEXT NOT NULL, payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS ix_vectora_dag_stage_claim
                ON vectora_dag_stages(status, claim_expires_at);
            """
        )
        await self.db.commit()

    async def create_plan(
        self,
        *,
        run_id: str,
        stages: Sequence[DAGStage],
        workspace_id: str | None = None,
    ) -> tuple[str, str]:
        validate_stages(stages)
        plan_id = str(uuid.uuid4())
        plan_hash = _hash_plan(stages)
        data = json.dumps(
            [
                {
                    "stage_id": s.stage_id,
                    "name": s.name,
                    "depends_on": list(s.depends_on),
                }
                for s in stages
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        await self.db.execute(
            "INSERT INTO vectora_dag_plans(plan_id,run_id,workspace_id,stages_json,plan_hash,created_at) VALUES(?,?,?,?,?,?)",
            (plan_id, run_id, workspace_id, data, plan_hash, _now()),
        )
        await self.db.commit()
        return plan_id, plan_hash

    async def approve_plan(
        self, plan_id: str, *, plan_hash: str, approver: str
    ) -> None:
        actor = approver.strip()
        if not actor:
            raise DAGValidationError("approver é obrigatório")
        cur = await self.db.execute(
            "UPDATE vectora_dag_plans SET status='approved', approved_by=?, approved_at=? "
            "WHERE plan_id=? AND plan_hash=? AND status='draft'",
            (actor, _now(), plan_id, plan_hash),
        )
        await self.db.commit()
        if cur.rowcount != 1:
            raise DAGValidationError(
                "plano inexistente, já aprovado ou hash divergente"
            )

    async def start_execution(
        self, plan_id: str, *, run_id: str, idempotency_key: str
    ) -> DAGExecution:
        """Start or recover the execution bound to an approved plan.

        The plan's ``run_id`` and the idempotency key are checked before any
        stage rows are created.  A concurrent insert is reconciled by loading
        the already committed execution, so retries never create duplicate
        effects.
        """
        async with self.db.execute(
            "SELECT stages_json,plan_hash,status,run_id FROM vectora_dag_plans WHERE plan_id=?",
            (plan_id,),
        ) as cur:
            plan = await cur.fetchone()
        if plan is None or plan["status"] != "approved":
            raise DAGValidationError("execução exige plano aprovado")
        if plan["run_id"] != run_id:
            raise DAGValidationError("execução deve usar o run_id do plano")
        # The approval is bound to the canonical stage hash.  Recompute it at
        # execution time as a fail-closed guard against a storage/admin edit
        # changing an approved plan after the human approval was recorded.
        try:
            persisted_stages = json.loads(plan["stages_json"])
            canonical_stages = [
                DAGStage(
                    stage_id=str(stage["stage_id"]),
                    name=str(stage["name"]),
                    depends_on=tuple(str(dep) for dep in stage["depends_on"]),
                )
                for stage in persisted_stages
            ]
            validate_stages(canonical_stages)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise DAGValidationError("plano aprovado está corrompido") from exc
        if not hmac.compare_digest(
            _hash_plan(canonical_stages), str(plan["plan_hash"])
        ):
            raise DAGValidationError(
                "hash do plano aprovado não corresponde ao conteúdo"
            )
        async with self.db.execute(
            "SELECT execution_id,plan_id,run_id,idempotency_key FROM vectora_dag_executions WHERE idempotency_key=?",
            (idempotency_key,),
        ) as cur:
            existing = await cur.fetchone()
        if existing is not None:
            if existing["plan_id"] != plan_id:
                raise DAGValidationError(
                    "idempotency_key já está vinculada a outro plano"
                )
            if existing["run_id"] != run_id:
                raise DAGValidationError(
                    "idempotency_key já está vinculada a outro run_id"
                )
            return DAGExecution(*existing)
        execution_id = str(uuid.uuid4())
        stages = persisted_stages
        try:
            await self.db.execute(
                "INSERT INTO vectora_dag_executions(execution_id,plan_id,run_id,idempotency_key,created_at) VALUES(?,?,?,?,?)",
                (execution_id, plan_id, run_id, idempotency_key, _now()),
            )
            await self.db.executemany(
                "INSERT INTO vectora_dag_stages(execution_id,stage_id,name,depends_on_json) VALUES(?,?,?,?)",
                [
                    (
                        execution_id,
                        s["stage_id"],
                        s["name"],
                        json.dumps(s["depends_on"]),
                    )
                    for s in stages
                ],
            )
            await self._event(
                execution_id, None, "execution_started", {"run_id": run_id}
            )
            await self.db.commit()
        except aiosqlite.IntegrityError:
            await self.db.rollback()
            return await self.start_execution(
                plan_id, run_id=run_id, idempotency_key=idempotency_key
            )
        return DAGExecution(execution_id, plan_id, run_id, idempotency_key)

    async def claim_ready_stage(
        self,
        execution_id: str,
        *,
        lease_seconds: int = 900,
        max_attempts: int = 3,
    ) -> tuple[str, str] | None:
        """Atomically claim the next dependency-ready stage with a lease."""
        if lease_seconds <= 0:
            raise DAGValidationError("lease_seconds deve ser positivo")
        if max_attempts <= 0:
            raise DAGValidationError("max_attempts deve ser positivo")
        async with self.db.execute(
            "SELECT status FROM vectora_dag_executions WHERE execution_id=?",
            (execution_id,),
        ) as execution_cur:
            execution = await execution_cur.fetchone()
        if execution is None or execution[0] != "running":
            return None
        async with self.db.execute(
            "SELECT stage_id,depends_on_json,attempt FROM vectora_dag_stages WHERE execution_id=? AND status='pending' ORDER BY rowid",
            (execution_id,),
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
            if int(row["attempt"]) >= max_attempts:
                continue
            deps = json.loads(row["depends_on_json"])
            if deps:
                marks = ",".join("?" for _ in deps)
                async with self.db.execute(
                    f"SELECT COUNT(*) FROM vectora_dag_stages WHERE execution_id=? AND stage_id IN ({marks}) AND status='passed'",  # noqa: S608  # nosec B608
                    (execution_id, *deps),
                ) as cur:
                    count_row = await cur.fetchone()
                    if count_row is None or count_row[0] != len(deps):
                        continue
            token = str(uuid.uuid4())
            expires = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
            cur = await self.db.execute(
                "UPDATE vectora_dag_stages SET status='running',attempt=attempt+1,claim_token=?,claim_expires_at=? "
                "WHERE execution_id=? AND stage_id=? AND status='pending'",
                (token, expires, execution_id, row["stage_id"]),
            )
            if cur.rowcount == 1:
                attempt = int(row["attempt"]) + 1
                await self._event(
                    execution_id,
                    row["stage_id"],
                    "stage_claimed",
                    {"attempt": attempt},
                )
                await self.db.commit()
                return row["stage_id"], token
        return None

    async def cancel_execution(
        self,
        execution_id: str,
        *,
        requested_by: str,
        reason: str = "cancelamento solicitado",
    ) -> bool:
        """Cancela cooperativamente uma execução ainda não terminal.

        A mutação é um CAS no estado da execução. Claims são invalidados antes
        dos eventos, impedindo que um worker antigo finalize a etapa depois do
        cancelamento. Workers já em execução devem observar o token perdido e
        interromper seu trabalho; nenhuma etapa nova pode ser reivindicada.
        """
        actor = requested_by.strip()
        if not actor:
            raise DAGValidationError("requested_by é obrigatório")
        message = reason.strip() or "cancelamento solicitado"
        cur = await self.db.execute(
            "UPDATE vectora_dag_executions SET status='cancelled' "
            "WHERE execution_id=? AND status='running'",
            (execution_id,),
        )
        if cur.rowcount != 1:
            await self.db.rollback()
            return False
        await self.db.execute(
            "UPDATE vectora_dag_stages SET status='cancelled',claim_token=NULL,"
            "claim_expires_at=NULL,error=? WHERE execution_id=? AND status IN ('pending','running')",
            (message, execution_id),
        )
        await self._event(
            execution_id,
            None,
            "execution_cancelled",
            {"requested_by": actor, "reason": message},
        )
        await self._event(
            execution_id,
            None,
            "execution_finished",
            {"status": "cancelled"},
        )
        await self.db.commit()
        return True

    async def heartbeat_stage(
        self,
        execution_id: str,
        stage_id: str,
        *,
        claim_token: str,
        lease_seconds: int = 900,
    ) -> bool:
        """Estende uma lease ativa sem alterar o estado da etapa."""
        if lease_seconds <= 0:
            raise DAGValidationError("lease_seconds deve ser positivo")
        expires = (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat()
        cur = await self.db.execute(
            "UPDATE vectora_dag_stages SET claim_expires_at=? "
            "WHERE execution_id=? AND stage_id=? AND status='running' "
            "AND claim_token=? AND claim_expires_at > ?",
            (expires, execution_id, stage_id, claim_token, _now()),
        )
        await self.db.commit()
        return cur.rowcount == 1

    @asynccontextmanager
    async def stage_lease_watchdog(
        self,
        execution_id: str,
        stage_id: str,
        *,
        claim_token: str,
        lease_seconds: int = 900,
        interval_seconds: float | None = None,
    ) -> AsyncGenerator[None]:
        """Mantém a lease viva enquanto o executor trabalha na etapa.

        O watchdog usa o mesmo ``claim_token`` e a renovação CAS de
        :meth:`heartbeat_stage`. Se outro worker recuperar a etapa, a
        renovação falha e o próximo ``finish_stage`` rejeita o token antigo.
        O intervalo padrão é um terço da lease, limitado a pelo menos 100 ms;
        testes e executores podem fornecer um intervalo menor explicitamente.
        """
        if lease_seconds <= 0:
            raise DAGValidationError("lease_seconds deve ser positivo")
        interval = (
            interval_seconds
            if interval_seconds is not None
            else max(0.1, lease_seconds / 3)
        )
        if interval <= 0:
            raise DAGValidationError("interval_seconds deve ser positivo")

        lost_claim = asyncio.Event()

        async def renew() -> None:
            while True:
                await asyncio.sleep(interval)
                if not await self.heartbeat_stage(
                    execution_id,
                    stage_id,
                    claim_token=claim_token,
                    lease_seconds=lease_seconds,
                ):
                    lost_claim.set()
                    return

        task = asyncio.create_task(renew())
        try:
            yield
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if lost_claim.is_set():
            raise DAGValidationError("lease da etapa foi perdida durante a execução")

    async def finish_stage(
        self,
        execution_id: str,
        stage_id: str,
        *,
        claim_token: str,
        passed: bool,
        output: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        status = "passed" if passed else "failed"
        cur = await self.db.execute(
            "UPDATE vectora_dag_stages SET status=?,claim_token=NULL,claim_expires_at=NULL,output_json=?,error=? "
            "WHERE execution_id=? AND stage_id=? AND status='running' "
            "AND claim_token=? AND claim_expires_at > ?",
            (
                status,
                json.dumps(output, ensure_ascii=False) if output is not None else None,
                error,
                execution_id,
                stage_id,
                claim_token,
                _now(),
            ),
        )
        if cur.rowcount != 1:
            await self.db.rollback()
            raise DAGValidationError("claim da etapa expirou ou já foi concluída")
        await self._event(
            execution_id, stage_id, f"stage_{status}", {"error": error} if error else {}
        )
        await self._refresh_execution_status(execution_id)
        await self.db.commit()

    async def _refresh_execution_status(self, execution_id: str) -> None:
        """Derive the execution status from its persisted stage states."""
        async with self.db.execute(
            "SELECT stage_id,status FROM vectora_dag_stages WHERE execution_id=?",
            (execution_id,),
        ) as cur:
            rows = await cur.fetchall()
        statuses = [str(row[1]) for row in rows]
        if not statuses:
            return
        # A failed stage makes the execution terminal.  Leaving unrelated
        # pending stages in place would keep the execution ``running``
        # forever and allow a later scheduler tick to claim work after the
        # failure.  Mark them explicitly as blocked before publishing the
        # execution terminal event.
        if "failed" in statuses:
            # A completed sibling must not erase a failure that already
            # happened in the execution. Wait for active claims to settle,
            # then publish the terminal failure exactly once.
            if "running" in statuses:
                return
            pending = [str(row[0]) for row in rows if row[1] == "pending"]
            for stage_id in pending:
                await self.db.execute(
                    "UPDATE vectora_dag_stages SET status='failed',error=? "
                    "WHERE execution_id=? AND stage_id=? AND status='pending'",
                    (
                        "etapa não executada porque outra etapa falhou",
                        execution_id,
                        stage_id,
                    ),
                )
                await self._event(
                    execution_id,
                    stage_id,
                    "stage_blocked",
                    {"reason": "dependency_failed"},
                )
            execution_status = "failed"
        elif any(state in {"pending", "running"} for state in statuses):
            return
        else:
            execution_status = "passed"
        cur = await self.db.execute(
            "UPDATE vectora_dag_executions SET status=? WHERE execution_id=? AND status='running'",
            (execution_status, execution_id),
        )
        if cur.rowcount == 1:
            await self._event(
                execution_id,
                None,
                "execution_finished",
                {"status": execution_status},
            )

    async def recover_expired_claims(self, *, max_attempts: int = 3) -> int:
        if max_attempts <= 0:
            raise DAGValidationError("max_attempts deve ser positivo")
        now = _now()
        async with self.db.execute(
            "SELECT execution_id,stage_id,attempt FROM vectora_dag_stages "
            "WHERE status='running' AND claim_expires_at IS NOT NULL "
            "AND claim_expires_at < ? AND attempt < ?",
            (now, max_attempts),
        ) as recover_cur:
            recover_rows = await recover_cur.fetchall()
        cur = await self.db.execute(
            "UPDATE vectora_dag_stages SET status='pending',claim_token=NULL,claim_expires_at=NULL "
            "WHERE status='running' AND claim_expires_at IS NOT NULL AND claim_expires_at < ? "
            "AND attempt < ?",
            (now, max_attempts),
        )
        for row in recover_rows:
            await self._event(
                row["execution_id"],
                row["stage_id"],
                "stage_recovered",
                {"attempt": int(row["attempt"])},
            )
        async with self.db.execute(
            "SELECT execution_id,stage_id FROM vectora_dag_stages "
            "WHERE status='running' AND claim_expires_at IS NOT NULL AND claim_expires_at < ? "
            "AND attempt >= ?",
            (now, max_attempts),
        ) as exhausted_cur:
            exhausted_rows = await exhausted_cur.fetchall()
        exhausted = await self.db.execute(
            "UPDATE vectora_dag_stages SET status='failed',claim_token=NULL,"
            "claim_expires_at=NULL,error=? "
            "WHERE status='running' AND claim_expires_at IS NOT NULL AND claim_expires_at < ? "
            "AND attempt >= ?",
            (
                "limite de tentativas excedido após expiração da lease",
                now,
                max_attempts,
            ),
        )
        for row in exhausted_rows:
            await self._event(
                row["execution_id"],
                row["stage_id"],
                "stage_recovery_exhausted",
                {"max_attempts": max_attempts},
            )
            await self._refresh_execution_status(row["execution_id"])
        await self.db.commit()
        return cur.rowcount + exhausted.rowcount

    async def _event(
        self,
        execution_id: str,
        stage_id: str | None,
        kind: str,
        payload: dict[str, Any],
    ) -> None:
        await self.db.execute(
            "INSERT INTO vectora_dag_events(event_id,execution_id,stage_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                execution_id,
                stage_id,
                kind,
                json.dumps(payload, ensure_ascii=False),
                _now(),
            ),
        )


async def recover_expired_dag_claims() -> int:
    """Reconcilia leases de etapas após reinício do scheduler."""
    from backend.scheduling.background_tasks import _get_db

    db = await _get_db()
    try:
        db.row_factory = aiosqlite.Row
        store = DAGStore(db)
        await store.setup()
        return await store.recover_expired_claims()
    finally:
        await db.close()
