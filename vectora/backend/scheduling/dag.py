"""Persistência mínima e durável para planos executáveis de subagentes.

O Kanban continua sendo a visão operacional das tarefas. Este módulo guarda a
identidade do plano aprovado e o estado de cada etapa, permitindo retomar uma
execução depois de reinício sem executar duas vezes uma etapa já concluída.
Todas as mutações usam CAS (claim token) e deixam um evento auditável.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Sequence
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
        cur = await self.db.execute(
            "UPDATE vectora_dag_plans SET status='approved', approved_by=?, approved_at=? "
            "WHERE plan_id=? AND plan_hash=? AND status='draft'",
            (approver, _now(), plan_id, plan_hash),
        )
        await self.db.commit()
        if cur.rowcount != 1:
            raise DAGValidationError(
                "plano inexistente, já aprovado ou hash divergente"
            )

    async def start_execution(
        self, plan_id: str, *, run_id: str, idempotency_key: str
    ) -> DAGExecution:
        async with self.db.execute(
            "SELECT stages_json,status FROM vectora_dag_plans WHERE plan_id=?",
            (plan_id,),
        ) as cur:
            plan = await cur.fetchone()
        if plan is None or plan["status"] != "approved":
            raise DAGValidationError("execução exige plano aprovado")
        async with self.db.execute(
            "SELECT execution_id,plan_id,run_id,idempotency_key FROM vectora_dag_executions WHERE idempotency_key=?",
            (idempotency_key,),
        ) as cur:
            existing = await cur.fetchone()
        if existing is not None:
            return DAGExecution(*existing)
        execution_id = str(uuid.uuid4())
        stages = json.loads(plan["stages_json"])
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
        self, execution_id: str, *, lease_seconds: int = 900
    ) -> tuple[str, str] | None:
        async with self.db.execute(
            "SELECT stage_id,depends_on_json,attempt FROM vectora_dag_stages WHERE execution_id=? AND status='pending' ORDER BY rowid",
            (execution_id,),
        ) as cur:
            rows = await cur.fetchall()
        for row in rows:
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
            "WHERE execution_id=? AND stage_id=? AND status='running' AND claim_token=?",
            (
                status,
                json.dumps(output, ensure_ascii=False) if output is not None else None,
                error,
                execution_id,
                stage_id,
                claim_token,
            ),
        )
        if cur.rowcount != 1:
            await self.db.rollback()
            raise DAGValidationError("claim da etapa expirou ou já foi concluída")
        await self._event(
            execution_id, stage_id, f"stage_{status}", {"error": error} if error else {}
        )
        await self.db.commit()

    async def recover_expired_claims(self) -> int:
        now = _now()
        cur = await self.db.execute(
            "UPDATE vectora_dag_stages SET status='pending',claim_token=NULL,claim_expires_at=NULL "
            "WHERE status='running' AND claim_expires_at IS NOT NULL AND claim_expires_at < ?",
            (now,),
        )
        await self.db.commit()
        return cur.rowcount

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
