"""``SessionStore`` — persistência de sessões/mensagens do motor de
conversa nativo sobre ``aiosqlite``: schema append-only ``sessions`` +
``messages`` + ``pending_approvals``.

- **Fork de conversa** (editar mensagem/regenerar) via ``parent_message_id``
  — a mensagem nova aponta pro ponto da cadeia de onde diverge; mensagens
  da branch anterior nunca são apagadas, só deixam de ser ``is_branch_head``.
- **HITL sobrevivendo a restart** via ``pending_approvals`` — persistido
  IMEDIATA e SINCRONAMENTE antes de qualquer espera, nunca só em memória
  de processo.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, TypedDict, cast

from backend.persistence.native.agent_run import (
    TERMINAL_AGENT_RUN_STATUSES,
    VALID_AGENT_RUN_STATUSES,
    AgentRun,
    AgentRunMetrics,
    AgentRunStatus,
    AgentRunTimelineEntry,
    redact_sensitive_data,
)
from backend.persistence.native.ledger import (
    NOTIFIABLE_EVENT_TYPES,
    LedgerEventType,
    LedgerEventTypeLiteral,
    RunDraft,
    ThreadLastMessage,
    ThreadLedgerEvent,
    ThreadSummary,
    ensure_thread_backfilled,
    make_approval_requested_key,
    make_approval_resolved_key,
    make_message_created_key,
    make_run_cancelled_key,
    make_run_failed_key,
)
from backend.persistence.native.schema import setup_sqlite_session_schema
from backend.services.context_manifest import (
    ContextCheckpointContract,
    ContextProjectionContract,
)
from backend.vtypes.contracts import ToolOption
from backend.vtypes.message import VMessage

if TYPE_CHECKING:
    import aiosqlite

    from backend.storage.sqlite.pool import AsyncConnectionPool


# Teto de segurança pra reconstrução recursiva da cadeia de mensagens
# (get_history_with_ids) — nenhuma conversa legítima chega perto disso;
# existe só pra um `parent_message_id` corrompido/cíclico não fazer a CTE
# recursar sem fim.
_CHAIN_DEPTH_CAP = 100_000

logger = logging.getLogger(__name__)


@dataclass
class _RunEvidenceLockEntry:
    lock: asyncio.Lock
    users: int = 0


class _RunEvidenceLockRegistry:
    """Retém locks somente enquanto há escritores ou waiters ativos."""

    def __init__(self) -> None:
        """Inicializa o estado da instância."""
        self._entries: dict[str, _RunEvidenceLockEntry] = {}

    @asynccontextmanager
    async def acquire(self, run_id: str) -> AsyncGenerator[None]:
        """Executa a operação acquire."""
        entry = self._entries.get(run_id)
        if entry is None:
            entry = _RunEvidenceLockEntry(lock=asyncio.Lock())
            self._entries[run_id] = entry
        entry.users += 1
        try:
            async with entry.lock:
                yield
        finally:
            current = self._entries.get(run_id)
            if current is entry:
                entry.users -= 1
                if entry.users == 0:
                    self._entries.pop(run_id, None)

    def __len__(self) -> int:
        """Implementa a operação especial __len__."""
        return len(self._entries)


def _now() -> str:
    """Executa a operação  now."""
    return datetime.now(UTC).isoformat()


def _map_legacy_session_run_status(status: str) -> str:
    """Mapeia status canônico de AgentRun para os valores válidos do CHECK de session_runs.

    session_runs CHECK: ('queued', 'running', 'streaming', 'completed', 'failed', 'cancelled')
    """
    if status in (
        "planned",
        "running",
        "waiting_approval",
        "verifying",
        "recovery_required",
    ):
        return "running"
    if status in ("passed", "degraded"):
        return "completed"
    return status


def _message_to_row(
    msg: VMessage,
) -> tuple[str, str, str | None, str | None, str | None, int | None]:
    """Executa a operação  message to row."""
    data = msg.to_dict()
    for block in data["content"]:
        if block.get("kind") == "image_url" and (
            block.get("asset_id")
            or block.get("attachment_name")
            or str(block.get("image_url") or "").casefold().startswith("data:")
        ):
            block["image_url"] = None
    content_json = json.dumps(data["content"], ensure_ascii=False)
    tool_calls_json = (
        json.dumps(data["tool_calls"], ensure_ascii=False)
        if data["tool_calls"]
        else None
    )
    return (
        data["role"],
        content_json,
        tool_calls_json,
        data["tool_call_id"],
        data["name"],
        # ``None`` means that the historical producer did not know whether
        # the message was an error. Preserve that state across both stores;
        # coercing it to ``False`` loses information during replay.
        data.get("is_error"),
    )


def _row_to_message(row: Any) -> VMessage:
    """Executa a operação  row to message."""
    _id, _parent, role, content_json, tool_calls_json, tool_call_id, name, is_error = (
        row
    )
    data = {
        "role": role,
        "content": json.loads(content_json),
        "tool_calls": json.loads(tool_calls_json) if tool_calls_json else [],
        "tool_call_id": tool_call_id,
        "name": name,
        "finish_reason": None,
        "is_error": None if is_error is None else bool(is_error),
    }
    return VMessage.from_dict(data)


class SessionSummary(TypedDict):
    """Uma linha de `SessionStore.list_all_sessions` — metadados da thread
    em `sessions` mais a contagem real de mensagens em `messages`."""

    thread_id: str
    user_id: str
    workspace_id: str | None
    mode: str
    created_at: str
    updated_at: str
    message_count: int


def _terminal_ledger_events(
    thread_id: str, run_id: str, turn_id: str, status: str, detail: str
) -> list[ThreadLedgerEvent] | None:
    """Evento de ledger que fecha um run terminal; `None` para status sem evento."""
    if status == "failed":
        event_key, event_type = make_run_failed_key(run_id), "run_failed"
    elif status == "cancelled":
        event_key, event_type = make_run_cancelled_key(run_id), "run_cancelled"
    else:
        return None
    return [
        ThreadLedgerEvent(
            thread_id=thread_id,
            event_key=event_key,
            type=event_type,
            run_id=run_id,
            turn_id=turn_id,
            payload={"detail": detail} if detail else {},
        )
    ]


class SessionStore:
    """Persistência async sobre um ``AsyncConnectionPool`` (aiosqlite) com os
    PRAGMAs de hardening já aplicados por conexão."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        """Inicializa o estado da instância."""
        self._pool = pool
        self._is_setup = False
        self._run_evidence_locks = _RunEvidenceLockRegistry()

    async def setup(self) -> None:
        """Cria as tabelas se não existirem. Idempotente — chamado
        automaticamente por todo método público antes de qualquer query."""
        if self._is_setup:
            return
        await setup_sqlite_session_schema(self._pool)
        self._is_setup = True

    async def create_session(
        self,
        thread_id: str,
        *,
        user_id: str,
        workspace_id: str | None = None,
        parent_thread_id: str | None = None,
        mode: str = "chat",
        permission_mode: str = "ask",
    ) -> None:
        """Executa a operação create session."""
        await self.setup()
        agora = _now()
        async with self._pool.acquire() as conn:
            try:
                await conn.execute(
                    "INSERT INTO sessions (thread_id, user_id, workspace_id, "
                    "parent_thread_id, mode, permission_mode, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(thread_id) DO UPDATE SET "
                    "workspace_id = CASE WHEN excluded.mode = 'chat' "
                    "THEN excluded.workspace_id ELSE COALESCE(excluded.workspace_id, sessions.workspace_id) END, "
                    "mode = excluded.mode, permission_mode = excluded.permission_mode, "
                    "updated_at = excluded.updated_at",
                    (
                        thread_id,
                        user_id,
                        workspace_id,
                        parent_thread_id,
                        mode,
                        permission_mode,
                        agora,
                        agora,
                    ),
                )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def start_run(
        self,
        thread_id: str,
        run_id: str,
        *,
        turn_id: str | None = None,
        detail: str | None = None,
        provider_model: str | None = None,
    ) -> None:
        """Persist a run before the provider is invoked.

        The unique `(thread_id, turn_id)` index makes reconnect/retry safe;
        an already-created run is moved back to ``running`` without creating
        a second execution record.
        """
        await self.setup()
        now = _now()
        async with self._pool.acquire() as conn:
            if turn_id is not None:
                existing_cur = await conn.execute(
                    "SELECT run_id FROM session_runs "
                    "WHERE thread_id = ? AND turn_id = ?",
                    (thread_id, turn_id),
                )
                existing = await existing_cur.fetchone()
                if existing is not None and str(existing[0]) != run_id:
                    raise ValueError(
                        f"turn_id {turn_id!r} já está associado ao run {existing[0]!r}"
                    )
            await conn.execute(
                "INSERT INTO session_runs "
                "(run_id, thread_id, turn_id, status, detail, provider_model, started_at, updated_at) "
                "VALUES (?, ?, ?, 'running', ?, ?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET status='running', "
                "detail=COALESCE(excluded.detail, session_runs.detail), "
                "provider_model=COALESCE(excluded.provider_model, session_runs.provider_model), "
                "updated_at=excluded.updated_at",
                (run_id, thread_id, turn_id, detail, provider_model, now, now),
            )
            await conn.commit()

    async def _upsert_agent_run_on_conn(
        self, conn: aiosqlite.Connection, run: AgentRun
    ) -> None:
        """Persiste ou atualiza uma execução do agente na conexão fornecida."""
        now = _now()
        existing_lineage = await (
            await conn.execute(
                "SELECT parent_run_id, correlation_id FROM agent_runs WHERE run_id = ?",
                (run.run_id,),
            )
        ).fetchone()
        if existing_lineage is not None:
            for field, current, incoming in (
                ("parent_run_id", existing_lineage[0], run.parent_run_id),
                ("correlation_id", existing_lineage[1], run.correlation_id),
            ):
                if (
                    current is not None
                    and incoming is not None
                    and str(current) != str(incoming)
                ):
                    raise ValueError(
                        f"{field} conflitante para AgentRun {run.run_id!r}"
                    )
        terminal_at = (
            (run.terminal_at or now)
            if run.status in TERMINAL_AGENT_RUN_STATUSES
            else run.terminal_at
        )
        redacted_plan_step = (
            cast("str | None", redact_sensitive_data(run.plan_step))
            if run.plan_step is not None
            else None
        )
        redacted_detail = (
            cast("str | None", redact_sensitive_data(run.detail))
            if run.detail is not None
            else None
        )
        redacted_error_code = (
            cast("str | None", redact_sensitive_data(run.error_code))
            if run.error_code is not None
            else None
        )
        tools_available = json.dumps(
            redact_sensitive_data(run.tools_available), ensure_ascii=False
        )
        tools_used = json.dumps(
            redact_sensitive_data(run.tools_used), ensure_ascii=False
        )
        approvals = json.dumps(redact_sensitive_data(run.approvals), ensure_ascii=False)
        files_touched = json.dumps(
            redact_sensitive_data(run.files_touched), ensure_ascii=False
        )
        commands_run = json.dumps(
            redact_sensitive_data(run.commands_run), ensure_ascii=False
        )
        tests_run = json.dumps(redact_sensitive_data(run.tests_run), ensure_ascii=False)
        artifacts = json.dumps(redact_sensitive_data(run.artifacts), ensure_ascii=False)
        metrics = json.dumps(run.metrics.model_dump(), ensure_ascii=False)
        evidence_list = list(run.evidence)
        if run.untrusted_content and not any(
            isinstance(item, dict) and item.get("untrusted_content") is True
            for item in evidence_list
        ):
            evidence_list.append({"kind": "provenance", "untrusted_content": True})
        if not any(
            isinstance(item, dict) and item.get("untrusted_content") is True
            for item in evidence_list
        ):
            previous_cursor = await conn.execute(
                "SELECT evidence_json FROM agent_runs WHERE run_id = ?",
                (run.run_id,),
            )
            previous = await previous_cursor.fetchone()
            if previous and previous[0]:
                try:
                    old = json.loads(previous[0])
                    if any(
                        isinstance(item, dict) and item.get("untrusted_content") is True
                        for item in old
                    ):
                        evidence_list.append(
                            {"kind": "provenance", "untrusted_content": True}
                        )
                except Exception:
                    logger.exception(
                        "session_store: falha ao ler proveniência anterior"
                    )
        evidence = json.dumps(redact_sensitive_data(evidence_list), ensure_ascii=False)

        # Mantém paridade com session_runs legado
        mapped_status = _map_legacy_session_run_status(run.status)
        provider_model = (
            f"{run.provider}:{run.model}"
            if run.provider and run.model
            else (run.model or None)
        )
        await conn.execute(
            "INSERT INTO session_runs "
            "(run_id, thread_id, turn_id, status, detail, provider_model, started_at, updated_at, terminal_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET "
            "status=CASE WHEN session_runs.status IN ('completed', 'failed', 'cancelled') "
            "THEN session_runs.status ELSE excluded.status END, "
            "detail=COALESCE(excluded.detail, session_runs.detail), "
            "provider_model=COALESCE(excluded.provider_model, session_runs.provider_model), "
            "updated_at=CASE WHEN session_runs.status IN ('completed', 'failed', 'cancelled') "
            "THEN session_runs.updated_at ELSE excluded.updated_at END, "
            "terminal_at=COALESCE(session_runs.terminal_at, excluded.terminal_at)",
            (
                run.run_id,
                run.thread_id,
                run.turn_id,
                mapped_status,
                redacted_detail,
                provider_model,
                run.started_at,
                now,
                terminal_at,
            ),
        )
        # Insere/atualiza na tabela canônica agent_runs
        await conn.execute(
            "INSERT INTO agent_runs ("
            "run_id, thread_id, contract_version, turn_id, user_id, workspace_id, model, provider, "
            "status, plan_step, checkpoint_id, tools_available_json, tools_used_json, approvals_json, "
            "files_touched_json, commands_run_json, tests_run_json, artifacts_json, "
            "metrics_json, evidence_json, detail, error_code, started_at, updated_at, terminal_at, "
            "parent_run_id, correlation_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(run_id) DO UPDATE SET "
            "status=CASE WHEN agent_runs.status IN ('passed', 'degraded', 'failed', 'cancelled') "
            "THEN agent_runs.status ELSE excluded.status END, "
            "plan_step=COALESCE(excluded.plan_step, agent_runs.plan_step), "
            "checkpoint_id=COALESCE(excluded.checkpoint_id, agent_runs.checkpoint_id), "
            "parent_run_id=CASE WHEN agent_runs.status IN ('passed', 'degraded', 'failed', 'cancelled') "
            "THEN agent_runs.parent_run_id ELSE COALESCE(excluded.parent_run_id, agent_runs.parent_run_id) END, "
            "correlation_id=CASE WHEN agent_runs.status IN ('passed', 'degraded', 'failed', 'cancelled') "
            "THEN agent_runs.correlation_id ELSE COALESCE(excluded.correlation_id, agent_runs.correlation_id) END, "
            "tools_available_json=excluded.tools_available_json, "
            "tools_used_json=excluded.tools_used_json, "
            "approvals_json=excluded.approvals_json, "
            "files_touched_json=excluded.files_touched_json, "
            "commands_run_json=excluded.commands_run_json, "
            "tests_run_json=excluded.tests_run_json, "
            "artifacts_json=excluded.artifacts_json, "
            "metrics_json=excluded.metrics_json, "
            "evidence_json=excluded.evidence_json, "
            "detail=COALESCE(excluded.detail, agent_runs.detail), "
            "error_code=COALESCE(excluded.error_code, agent_runs.error_code), "
            "updated_at=CASE WHEN agent_runs.status IN ('passed', 'degraded', 'failed', 'cancelled') "
            "THEN agent_runs.updated_at ELSE excluded.updated_at END, "
            "terminal_at=COALESCE(agent_runs.terminal_at, excluded.terminal_at)",
            (
                run.run_id,
                run.thread_id,
                run.contract_version,
                run.turn_id,
                run.user_id,
                run.workspace_id,
                run.model,
                run.provider,
                run.status,
                redacted_plan_step,
                run.checkpoint_id,
                tools_available,
                tools_used,
                approvals,
                files_touched,
                commands_run,
                tests_run,
                artifacts,
                metrics,
                evidence,
                redacted_detail,
                redacted_error_code,
                run.started_at,
                now,
                terminal_at,
                run.parent_run_id,
                run.correlation_id,
            ),
        )

    async def upsert_agent_run(
        self,
        run: AgentRun,
    ) -> None:
        """Cria ou atualiza uma execução do agente com redação de dados sensíveis."""
        await self.setup()
        async with self._pool.acquire() as conn:
            await self._upsert_agent_run_on_conn(conn, run)
            await conn.commit()

    async def update_agent_run_status(
        self,
        run_id: str,
        *,
        status: AgentRunStatus,
        plan_step: str | None = None,
        detail: str | None = None,
        error_code: str | None = None,
        metrics: AgentRunMetrics | None = None,
    ) -> None:
        """Transiciona o status de uma run atomicamente."""
        if status not in VALID_AGENT_RUN_STATUSES:
            raise ValueError(f"status de AgentRun inválido: {status!r}")
        await self.setup()
        now = _now()
        terminal_at = now if status in TERMINAL_AGENT_RUN_STATUSES else None
        redacted_plan_step = (
            cast("str | None", redact_sensitive_data(plan_step))
            if plan_step is not None
            else None
        )
        redacted_detail = (
            cast("str | None", redact_sensitive_data(detail))
            if detail is not None
            else None
        )
        redacted_error_code = (
            cast("str | None", redact_sensitive_data(error_code))
            if error_code is not None
            else None
        )
        metrics_json = (
            json.dumps(metrics.model_dump(), ensure_ascii=False) if metrics else None
        )

        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE agent_runs SET "
                "status = ?, "
                "plan_step = COALESCE(?, plan_step), "
                "detail = COALESCE(?, detail), "
                "error_code = COALESCE(?, error_code), "
                "metrics_json = COALESCE(?, metrics_json), "
                "updated_at = ?, "
                "terminal_at = COALESCE(terminal_at, ?) "
                "WHERE run_id = ? AND status NOT IN ('passed', 'degraded', 'failed', 'cancelled')",
                (
                    status,
                    redacted_plan_step,
                    redacted_detail,
                    redacted_error_code,
                    metrics_json,
                    now,
                    terminal_at,
                    run_id,
                ),
            )
            # Atualiza também session_runs para compatibilidade com legados
            mapped_status = _map_legacy_session_run_status(status)
            await conn.execute(
                "UPDATE session_runs SET "
                "status = ?, "
                "detail = COALESCE(?, detail), "
                "error_code = COALESCE(?, error_code), "
                "updated_at = ?, "
                "terminal_at = COALESCE(terminal_at, ?) "
                "WHERE run_id = ? AND status NOT IN ('completed', 'failed', 'cancelled')",
                (
                    mapped_status,
                    redacted_detail,
                    redacted_error_code,
                    now,
                    terminal_at,
                    run_id,
                ),
            )
            await conn.commit()

    async def record_run_evidence(
        self,
        run_id: str,
        *,
        evidence: dict[str, Any] | None = None,
        artifact: dict[str, Any] | None = None,
        command: dict[str, Any] | None = None,
        test_result: dict[str, Any] | None = None,
        tool_used: str | None = None,
        approval: dict[str, Any] | None = None,
        file_touched: dict[str, Any] | None = None,
    ) -> None:
        """Registra evidências incrementais em um AgentRun com sanitização de segredos de forma atômica."""
        await self.setup()
        async with self._run_evidence_locks.acquire(run_id):
            async with self._pool.acquire() as conn:
                await conn.execute("BEGIN IMMEDIATE")
                try:
                    run = await self._get_agent_run_on_conn(conn, run_id)
                    if run is None:
                        await conn.commit()
                        return
                    if evidence:
                        clean_evidence = redact_sensitive_data(evidence)
                        evidence_id = (
                            clean_evidence.get("evidence_id")
                            or clean_evidence.get("projection_id")
                            if isinstance(clean_evidence, dict)
                            else None
                        )
                        if evidence_id is None or not any(
                            isinstance(item, dict)
                            and (item.get("evidence_id") or item.get("projection_id"))
                            == evidence_id
                            for item in run.evidence
                        ):
                            run.evidence.append(clean_evidence)
                    if artifact:
                        clean_artifact = redact_sensitive_data(artifact)
                        artifact_id = (
                            clean_artifact.get("artifact_id")
                            if isinstance(clean_artifact, dict)
                            else None
                        )
                        if artifact_id is None or not any(
                            isinstance(item, dict)
                            and item.get("artifact_id") == artifact_id
                            for item in run.artifacts
                        ):
                            run.artifacts.append(clean_artifact)
                    if command:
                        run.commands_run.append(redact_sensitive_data(command))
                    if test_result:
                        run.tests_run.append(redact_sensitive_data(test_result))
                    if tool_used and tool_used not in run.tools_used:
                        run.tools_used.append(tool_used)
                    if approval:
                        run.approvals.append(redact_sensitive_data(approval))
                    if file_touched:
                        run.files_touched.append(redact_sensitive_data(file_touched))
                    await self._upsert_agent_run_on_conn(conn, run)
                    await conn.commit()
                except Exception:
                    await conn.execute("ROLLBACK")
                    raise

    async def save_context_projection(
        self,
        thread_id: str,
        run_id: str,
        projection: ContextProjectionContract | Mapping[str, object],
    ) -> None:
        """Persiste a projeção versionada fora do transcript canônico."""
        await self.setup()
        # Token/sequence counters are numeric metadata, not credentials.  The
        # generic redactor intentionally treats keys containing ``token`` as
        # sensitive, so redact only the free-form projection payload while
        # preserving counters for ordering and budget accounting.
        projection_values = (
            projection.model_dump()
            if isinstance(projection, ContextProjectionContract)
            else projection
        )
        clean: dict[str, object] = {
            key: value
            if key
            in {
                "start_seq",
                "end_seq",
                "tokens_before",
                "tokens_after",
            }
            else redact_sensitive_data(value)
            for key, value in projection_values.items()
        }
        if not isinstance(clean, dict) or not clean.get("projection_id"):
            raise ValueError("projeção sem projection_id")
        # ``thread_id`` already participates in the table primary key.  Keep
        # the projection identity opaque and stable; embedding the thread in
        # this column made the persisted key differ from the contract and
        # made migrations/reconciliation unable to address a projection by
        # its public id.
        storage_projection_id = str(clean["projection_id"])
        projection_row = (
            storage_projection_id,
            thread_id,
            run_id,
            str(clean.get("source_transcript_hash", "")),
            int(cast("int", clean.get("start_seq", 0))),
            int(cast("int", clean.get("end_seq", 0))),
            str(clean.get("model", "")),
            str(clean.get("policy_version", "")),
            int(cast("int", clean.get("tokens_before", 0))),
            int(cast("int", clean.get("tokens_after", 0))),
            json.dumps(clean.get("preserved_decisions", []), ensure_ascii=False),
            json.dumps(clean.get("preserved_errors", []), ensure_ascii=False),
            json.dumps(clean.get("preserved_approvals", []), ensure_ascii=False),
            json.dumps(clean.get("pending_tool_calls", []), ensure_ascii=False),
            json.dumps(clean.get("omitted_sequences", []), ensure_ascii=False),
            json.dumps(clean.get("content"), ensure_ascii=False)
            if clean.get("content") is not None
            else None,
        )
        async with self._pool.acquire() as conn:
            existing = await (
                await conn.execute(
                    "SELECT projection_id, thread_id, run_id, source_hash, "
                    "covered_from_sequence, covered_to_sequence, model, policy_version, "
                    "tokens_before, tokens_after, preserved_decisions_json, "
                    "preserved_errors_json, preserved_approvals_json, "
                    "pending_tool_calls_json, omitted_sequences_json, content_json "
                    "FROM context_projections WHERE thread_id=? AND projection_id=?",
                    (thread_id, storage_projection_id),
                )
            ).fetchone()
            if existing is not None:
                if tuple(existing) != projection_row:
                    raise ValueError(
                        f"projeção {storage_projection_id!r} já existe com conteúdo diferente"
                    )
                return
            await conn.execute(
                "INSERT INTO context_projections ("
                "projection_id, thread_id, run_id, source_hash, covered_from_sequence, "
                "covered_to_sequence, model, policy_version, tokens_before, tokens_after, "
                "preserved_decisions_json, preserved_errors_json, preserved_approvals_json, "
                "pending_tool_calls_json, omitted_sequences_json, content_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*projection_row, str(clean.get("created_at", _now()))),
            )
            await conn.commit()

    async def get_latest_context_projection(
        self,
        thread_id: str,
        *,
        policy_version: str | None = None,
        source_transcript_hash: str | None = None,
    ) -> dict[str, object] | None:
        """Carrega projeção compatível com política e hash, se informados."""
        await self.setup()
        async with self._pool.acquire() as conn:
            clauses = ["thread_id = ?"]
            params: list[str] = [thread_id]
            if policy_version is not None:
                clauses.append("policy_version = ?")
                params.append(policy_version)
            if source_transcript_hash is not None:
                clauses.append("source_hash = ?")
                params.append(source_transcript_hash)
            cur = await conn.execute(
                "SELECT projection_id, run_id, source_hash, covered_from_sequence, "  # nosec B608
                "covered_to_sequence, model, policy_version, tokens_before, tokens_after, "
                "preserved_decisions_json, preserved_errors_json, preserved_approvals_json, "
                "pending_tool_calls_json, omitted_sequences_json, content_json, created_at "
                "FROM context_projections WHERE "
                + " AND ".join(clauses)
                + " ORDER BY covered_to_sequence DESC, created_at DESC LIMIT 1",
                params,
            )  # nosec B608 — clauses are fixed column names; values stay bound
            row = await cur.fetchone()
        if row is None:
            return None
        return {
            "projection_id": str(row[0]),
            "run_id": row[1],
            "source_hash": row[2],
            "covered_from_sequence": row[3],
            "covered_to_sequence": row[4],
            "model": row[5],
            "policy_version": row[6],
            "tokens_before": row[7],
            "tokens_after": row[8],
            "preserved_decisions": json.loads(row[9] or "[]"),
            "preserved_errors": json.loads(row[10] or "[]"),
            "preserved_approvals": json.loads(row[11] or "[]"),
            "pending_tool_calls": json.loads(row[12] or "[]"),
            "omitted_sequences": json.loads(row[13] or "[]"),
            "content": json.loads(row[14]) if row[14] else None,
            "created_at": row[15],
        }

    async def save_context_checkpoint(
        self,
        thread_id: str,
        run_id: str,
        checkpoint: ContextCheckpointContract | Mapping[str, object],
    ) -> None:
        """Persiste o estado mínimo para retomada por snapshot + deltas."""
        await self.setup()
        checkpoint_values = (
            checkpoint.model_dump()
            if isinstance(checkpoint, ContextCheckpointContract)
            else checkpoint
        )
        clean: dict[str, object] = {
            key: redact_sensitive_data(value)
            for key, value in checkpoint_values.items()
        }
        if not clean.get("checkpoint_id"):
            raise ValueError("checkpoint sem checkpoint_id")
        last_sequence = int(cast("int", clean.get("last_sequence", 0)))
        source_hash = str(clean.get("source_hash", ""))
        state_json = json.dumps(clean.get("state", {}), ensure_ascii=False)
        artifact_refs_json = json.dumps(
            clean.get("artifact_refs", []), ensure_ascii=False
        )
        async with self._pool.acquire() as conn:
            existing = await (
                await conn.execute(
                    "SELECT checkpoint_id, schema_version, state_json, artifact_refs_json "
                    "FROM context_checkpoints WHERE thread_id=? AND run_id=? "
                    "AND last_sequence=? AND source_hash=?",
                    (thread_id, run_id, last_sequence, source_hash),
                )
            ).fetchone()
            if existing is not None:
                expected = (
                    str(clean["checkpoint_id"]),
                    str(clean.get("schema_version", "context.v1")),
                    state_json,
                    artifact_refs_json,
                )
                if tuple(existing) != expected:
                    raise ValueError(
                        "checkpoint idêntico possui estado divergente; replay rejeitado"
                    )
                return
            await conn.execute(
                "INSERT INTO context_checkpoints ("
                "checkpoint_id, thread_id, run_id, last_sequence, source_hash, "
                "schema_version, state_json, artifact_refs_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(thread_id, run_id, last_sequence, source_hash) DO NOTHING",
                (
                    str(clean["checkpoint_id"]),
                    thread_id,
                    run_id,
                    last_sequence,
                    source_hash,
                    str(clean.get("schema_version", "context.v1")),
                    state_json,
                    artifact_refs_json,
                    str(clean.get("created_at", _now())),
                ),
            )
            await conn.commit()

    async def get_latest_context_checkpoint(
        self,
        thread_id: str,
        *,
        source_hash: str | None = None,
        run_id: str | None = None,
    ) -> dict[str, object] | None:
        """Carrega o checkpoint mais recente compatível com o transcript."""
        await self.setup()
        async with self._pool.acquire() as conn:
            clauses = ["thread_id = ?"]
            params: list[str] = [thread_id]
            if source_hash is not None:
                clauses.append("source_hash = ?")
                params.append(source_hash)
            if run_id is not None:
                clauses.append("run_id = ?")
                params.append(run_id)
            cur = await conn.execute(
                "SELECT checkpoint_id, run_id, last_sequence, source_hash, "  # nosec B608 - clauses are fixed column names
                "schema_version, state_json, artifact_refs_json, created_at "
                "FROM context_checkpoints WHERE "
                + " AND ".join(clauses)
                + " ORDER BY last_sequence DESC, created_at DESC LIMIT 1",
                params,
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return {
            "checkpoint_id": row[0],
            "run_id": row[1],
            "last_sequence": row[2],
            "source_hash": row[3],
            "schema_version": row[4],
            "state": json.loads(row[5] or "{}"),
            "artifact_refs": json.loads(row[6] or "[]"),
            "created_at": row[7],
        }

    async def purge_context_records(
        self, older_than: str, *, limit: int = 500
    ) -> dict[str, int]:
        """Remove projections/checkpoints older than a retention cutoff.

        The bounded batch is intentional: maintenance must not hold the
        SQLite write lock while trying to delete an unbounded history.  IDs
        are selected before deletion so the two tables are counted and
        removed atomically, while newer context remains untouched.
        """
        if not older_than.strip():
            raise ValueError("older_than não pode ser vazio")
        if limit < 1:
            raise ValueError("limit deve ser positivo")
        safe_limit = min(int(limit), 5000)
        await self.setup()
        async with self._pool.acquire() as conn:
            try:
                projection_cur = await conn.execute(
                    "SELECT thread_id, projection_id FROM context_projections "
                    "WHERE created_at < ? ORDER BY created_at ASC LIMIT ?",
                    (older_than, safe_limit),
                )
                projection_keys = [
                    (str(row[0]), str(row[1]))
                    for row in await projection_cur.fetchall()
                ]
                checkpoint_cur = await conn.execute(
                    "SELECT thread_id, checkpoint_id FROM context_checkpoints "
                    "WHERE created_at < ? ORDER BY created_at ASC LIMIT ?",
                    (older_than, safe_limit),
                )
                checkpoint_keys = [
                    (str(row[0]), str(row[1]))
                    for row in await checkpoint_cur.fetchall()
                ]
                for thread_id, projection_id in projection_keys:
                    await conn.execute(
                        "DELETE FROM context_projections "
                        "WHERE thread_id = ? AND projection_id = ?",
                        (thread_id, projection_id),
                    )
                for thread_id, checkpoint_id in checkpoint_keys:
                    await conn.execute(
                        "DELETE FROM context_checkpoints "
                        "WHERE thread_id = ? AND checkpoint_id = ?",
                        (thread_id, checkpoint_id),
                    )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
        return {
            "projections": len(projection_keys),
            "checkpoints": len(checkpoint_keys),
        }

    async def _get_agent_run_on_conn(
        self, conn: aiosqlite.Connection, run_id: str
    ) -> AgentRun | None:
        """Carrega e desserializa um AgentRun na conexão fornecida."""
        cur = await conn.execute(
            "SELECT run_id, thread_id, contract_version, turn_id, user_id, workspace_id, "
            "model, provider, status, plan_step, checkpoint_id, tools_available_json, "
            "tools_used_json, approvals_json, files_touched_json, commands_run_json, "
            "tests_run_json, artifacts_json, metrics_json, evidence_json, "
            "detail, error_code, started_at, updated_at, terminal_at, parent_run_id, correlation_id "
            "FROM agent_runs WHERE run_id = ?",
            (run_id,),
        )
        row = await cur.fetchone()
        if row is None:
            # Fallback para session_runs se tiver sido registrado antes da migração
            cur_legacy = await conn.execute(
                "SELECT run_id, thread_id, turn_id, status, detail, error_code, "
                "provider_model, started_at, updated_at, terminal_at "
                "FROM session_runs WHERE run_id = ?",
                (run_id,),
            )
            leg = await cur_legacy.fetchone()
            if leg is None:
                return None
            st = leg[3]
            canonical_st: AgentRunStatus = (
                "passed"
                if st == "completed"
                else cast(
                    "AgentRunStatus",
                    st if st in VALID_AGENT_RUN_STATUSES else "running",
                )
            )
            prov_model = str(leg[6] or "")
            prov, _, mod = prov_model.partition(":")
            return AgentRun(
                run_id=str(leg[0]),
                thread_id=str(leg[1]),
                turn_id=str(leg[2]) if leg[2] else None,
                status=canonical_st,
                detail=str(leg[4]) if leg[4] else None,
                error_code=str(leg[5]) if leg[5] else None,
                provider=prov if mod else "",
                model=mod or prov,
                started_at=str(leg[7]),
                updated_at=str(leg[8]),
                terminal_at=str(leg[9]) if leg[9] else None,
            )

        evidence_data = json.loads(row[19] or "[]")
        is_untrusted = any(
            isinstance(item, dict) and item.get("untrusted_content") is True
            for item in evidence_data
        )
        return AgentRun(
            run_id=str(row[0]),
            thread_id=str(row[1]),
            contract_version=int(row[2] or 1),
            turn_id=str(row[3]) if row[3] else None,
            user_id=str(row[4] or ""),
            workspace_id=str(row[5]) if row[5] else None,
            model=str(row[6] or ""),
            provider=str(row[7] or ""),
            status=cast("AgentRunStatus", row[8]),
            plan_step=str(row[9]) if row[9] else None,
            checkpoint_id=str(row[10]) if row[10] else None,
            tools_available=json.loads(row[11] or "[]"),
            tools_used=json.loads(row[12] or "[]"),
            approvals=json.loads(row[13] or "[]"),
            files_touched=json.loads(row[14] or "[]"),
            commands_run=json.loads(row[15] or "[]"),
            tests_run=json.loads(row[16] or "[]"),
            artifacts=json.loads(row[17] or "[]"),
            metrics=AgentRunMetrics(**json.loads(row[18] or "{}")),
            evidence=evidence_data,
            untrusted_content=is_untrusted,
            detail=str(row[20]) if row[20] else None,
            error_code=str(row[21]) if row[21] else None,
            started_at=str(row[22]),
            updated_at=str(row[23]),
            terminal_at=str(row[24]) if row[24] else None,
            parent_run_id=str(row[25]) if row[25] else None,
            correlation_id=str(row[26]) if row[26] else None,
        )

    async def get_agent_run(self, run_id: str) -> AgentRun | None:
        """Obtém uma execução persistida por run_id."""
        await self.setup()
        async with self._pool.acquire() as conn:
            return await self._get_agent_run_on_conn(conn, run_id)

    async def list_agent_runs(
        self, thread_id: str, *, limit: int = 50
    ) -> list[AgentRun]:
        """Lista todas as execuções associadas a uma thread ordenadas pelas mais recentes primeiro."""
        await self.setup()
        safe_limit = max(1, min(500, int(limit)))
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT run_id, thread_id, contract_version, turn_id, user_id, workspace_id, "
                "model, provider, status, plan_step, checkpoint_id, tools_available_json, "
                "tools_used_json, approvals_json, files_touched_json, commands_run_json, "
                "tests_run_json, artifacts_json, metrics_json, evidence_json, "
                "detail, error_code, started_at, updated_at, terminal_at, parent_run_id, correlation_id "
                "FROM agent_runs WHERE thread_id = ? "
                "ORDER BY started_at DESC LIMIT ?",
                (thread_id, safe_limit),
            )
            rows = await cur.fetchall()
            runs: list[AgentRun] = []
            for r in rows:
                evidence_data = json.loads(r[19] or "[]")
                is_untrusted = any(
                    isinstance(item, dict) and item.get("untrusted_content") is True
                    for item in evidence_data
                )
                runs.append(
                    AgentRun(
                        run_id=str(r[0]),
                        thread_id=str(r[1]),
                        contract_version=int(r[2] or 1),
                        turn_id=str(r[3]) if r[3] else None,
                        user_id=str(r[4] or ""),
                        workspace_id=str(r[5]) if r[5] else None,
                        model=str(r[6] or ""),
                        provider=str(r[7] or ""),
                        status=cast("AgentRunStatus", r[8]),
                        plan_step=str(r[9]) if r[9] else None,
                        checkpoint_id=str(r[10]) if r[10] else None,
                        tools_available=json.loads(r[11] or "[]"),
                        tools_used=json.loads(r[12] or "[]"),
                        approvals=json.loads(r[13] or "[]"),
                        files_touched=json.loads(r[14] or "[]"),
                        commands_run=json.loads(r[15] or "[]"),
                        tests_run=json.loads(r[16] or "[]"),
                        artifacts=json.loads(r[17] or "[]"),
                        metrics=AgentRunMetrics(**json.loads(r[18] or "{}")),
                        evidence=evidence_data,
                        untrusted_content=is_untrusted,
                        detail=str(r[20]) if r[20] else None,
                        error_code=str(r[21]) if r[21] else None,
                        started_at=str(r[22]),
                        updated_at=str(r[23]),
                        terminal_at=str(r[24]) if r[24] else None,
                        parent_run_id=str(r[25]) if r[25] else None,
                        correlation_id=str(r[26]) if r[26] else None,
                    )
                )
            return runs

    async def get_agent_run_timeline(
        self, run_id: str, *, limit: int = 500, offset: int = 0
    ) -> list[AgentRunTimelineEntry]:
        """Recupera uma página ordenada dos eventos correlacionados ao run."""
        limit = max(1, min(limit, 500))
        offset = max(0, offset)
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT seq, event_key, type, created_at, payload_json "
                "FROM thread_events WHERE run_id = ? ORDER BY seq ASC LIMIT ? OFFSET ?",
                (run_id, limit, offset),
            )
            rows = await cur.fetchall()
            return [
                AgentRunTimelineEntry(
                    seq=r[0],
                    event_key=str(r[1]),
                    type=str(r[2]),
                    created_at=str(r[3]),
                    payload=json.loads(r[4] or "{}"),
                )
                for r in rows
            ]

    async def update_run(
        self,
        run_id: str,
        *,
        status: str | None = None,
        detail: str | None = None,
        error_code: str | None = None,
        partial_text: str | None = None,
        provider_model: str | None = None,
    ) -> None:
        """Advance a run state atomically and record its terminal timestamp.

        If `status` is None, preserves the current status and only updates
        the supplied metadata fields (e.g. `provider_model`).
        """
        if status is not None and status not in {
            "queued",
            "running",
            "streaming",
            "completed",
            "failed",
            "cancelled",
        }:
            raise ValueError(f"status de run inválido: {status!r}")
        await self.setup()
        now = _now()
        terminal_at = now if status in {"completed", "failed", "cancelled"} else None
        redacted_detail = redact_sensitive_data(detail) if detail is not None else None
        redacted_error_code = (
            redact_sensitive_data(error_code) if error_code is not None else None
        )
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE session_runs SET "
                "status = COALESCE(?, status), "
                "detail = COALESCE(?, detail), "
                "error_code = COALESCE(?, error_code), "
                "partial_text = COALESCE(?, partial_text), updated_at = ?, "
                "provider_model = COALESCE(?, provider_model), "
                "terminal_at = COALESCE(?, terminal_at) "
                "WHERE run_id = ? "
                "AND (status IS NULL OR status NOT IN ('completed', 'failed', 'cancelled'))",
                (
                    status,
                    redacted_detail,
                    redacted_error_code,
                    partial_text,
                    now,
                    provider_model,
                    terminal_at,
                    run_id,
                ),
            )
            # Atualiza também na tabela canônica agent_runs se presente
            if status is not None:
                canonical_status: AgentRunStatus = (
                    "passed"
                    if status == "completed"
                    else cast(
                        "AgentRunStatus",
                        status if status in VALID_AGENT_RUN_STATUSES else "running",
                    )
                )
                await conn.execute(
                    "UPDATE agent_runs SET "
                    "status = ?, "
                    "detail = COALESCE(?, detail), "
                    "error_code = COALESCE(?, error_code), "
                    "updated_at = ?, "
                    "terminal_at = COALESCE(?, terminal_at) "
                    "WHERE run_id = ? "
                    "AND (status IS NULL OR status NOT IN ('passed', 'degraded', 'failed', 'cancelled'))",
                    (
                        canonical_status,
                        redacted_detail,
                        redacted_error_code,
                        now,
                        terminal_at,
                        run_id,
                    ),
                )
            await conn.commit()

    async def reconcile_abandoned_runs(self, *, older_than_seconds: float) -> int:
        """Move stale non-terminal runs to an explicit failed state."""
        await self.setup()
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
        now = _now()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "UPDATE session_runs SET status='failed', detail=?, "
                "error_code='RUN_ABANDONED', updated_at=?, terminal_at=? "
                "WHERE status IN ('queued', 'running', 'streaming') "
                "AND updated_at < ?",
                (
                    "A execução foi encerrada sem confirmação do processo.",
                    now,
                    now,
                    cutoff.isoformat(),
                ),
            )
            await conn.commit()
        return int(cur.rowcount or 0)

    async def append_message(
        self,
        thread_id: str,
        msg: VMessage,
        *,
        parent_message_id: int | None = None,
        turn_id: str | None = None,
        run_id: str | None = None,
    ) -> int:
        """Persiste `msg` e devolve o `id` gerado, delegando para `append_message_recorded`."""
        msg_id, _ = await self.append_message_recorded(
            thread_id,
            msg,
            parent_message_id=parent_message_id,
            turn_id=turn_id,
            run_id=run_id,
        )
        return msg_id

    async def append_message_recorded(
        self,
        thread_id: str,
        msg: VMessage,
        *,
        parent_message_id: int | None = None,
        turn_id: str | None = None,
        run_id: str | None = None,
        extra_events: list[ThreadLedgerEvent] | None = None,
        terminal_status: str | None = None,
        terminal_detail: str | None = None,
    ) -> tuple[int, int]:
        """Persiste `msg` e o evento `message_created` atomicamente no ledger,
        devolvendo `(message_id, seq)`. Aceita eventos extras na mesma transação."""
        await self.setup()
        await ensure_thread_backfilled(self._pool, thread_id)
        agora = _now()
        role, content_json, tool_calls_json, tool_call_id, name, is_error = (
            _message_to_row(msg)
        )
        async with self._pool.acquire() as conn:
            try:
                await conn.execute("BEGIN IMMEDIATE")
                if turn_id is not None:
                    cur_existing = await conn.execute(
                        "SELECT id FROM messages WHERE thread_id = ? AND turn_id = ?",
                        (thread_id, turn_id),
                    )
                    existing = await cur_existing.fetchone()
                    if existing is not None:
                        # Buscar seq do evento message_created existente
                        existing_id = int(existing[0])
                        cur_seq = await conn.execute(
                            "SELECT seq FROM thread_events WHERE thread_id = ? AND message_id = ?",
                            (thread_id, existing_id),
                        )
                        row_seq = await cur_seq.fetchone()
                        seq = int(row_seq[0]) if row_seq is not None else 0
                        await conn.rollback()
                        return existing_id, seq

                if terminal_status is not None:
                    if terminal_status not in {"completed", "failed", "cancelled"}:
                        raise ValueError(
                            f"status terminal inválido: {terminal_status!r}"
                        )
                    terminal_at = agora
                    redacted_detail = redact_sensitive_data(terminal_detail or "")
                    await conn.execute(
                        "UPDATE session_runs SET status = ?, detail = COALESCE(?, detail), "
                        "updated_at = ?, terminal_at = COALESCE(terminal_at, ?) "
                        "WHERE run_id = ? AND status NOT IN "
                        "('completed', 'failed', 'cancelled')",
                        (
                            terminal_status,
                            redacted_detail or None,
                            agora,
                            terminal_at,
                            run_id,
                        ),
                    )
                    canonical_status = (
                        "passed" if terminal_status == "completed" else terminal_status
                    )
                    await conn.execute(
                        "UPDATE agent_runs SET status = ?, detail = COALESCE(?, detail), "
                        "updated_at = ?, terminal_at = COALESCE(terminal_at, ?) "
                        "WHERE run_id = ? AND status NOT IN "
                        "('passed', 'degraded', 'failed', 'cancelled')",
                        (
                            canonical_status,
                            redacted_detail or None,
                            agora,
                            terminal_at,
                            run_id,
                        ),
                    )

                cur = await conn.execute(
                    "INSERT INTO messages (thread_id, parent_message_id, role, "
                    "content_json, tool_calls_json, tool_call_id, name, is_error, "
                    "turn_id, run_id, edited_files_json, is_branch_head, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                    (
                        thread_id,
                        parent_message_id,
                        role,
                        content_json,
                        tool_calls_json,
                        tool_call_id,
                        name,
                        is_error,
                        turn_id,
                        run_id,
                        None,
                        agora,
                    ),
                )
                new_id = cur.lastrowid
                if new_id is None:
                    erro = "INSERT em `messages` não gerou lastrowid"
                    raise RuntimeError(erro)

                await conn.execute(
                    "UPDATE messages SET is_branch_head = 0 "
                    "WHERE thread_id = ? AND id != ?",
                    (thread_id, new_id),
                )
                await conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE thread_id = ?",
                    (agora, thread_id),
                )

                # Obter próximo seq do ledger
                cur_max = await conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM thread_events WHERE thread_id = ?",
                    (thread_id,),
                )
                row_max = await cur_max.fetchone()
                current_seq = int(row_max[0]) if row_max else 0

                # Gravar eventos extras se houver (ex: tool_finished)
                if extra_events:
                    for extra in extra_events:
                        current_seq += 1
                        await conn.execute(
                            "INSERT INTO thread_events (thread_id, seq, event_key, type, "
                            "turn_id, run_id, message_id, tool_call_id, interrupt_id, payload_json, created_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                            "ON CONFLICT(thread_id, event_key) DO NOTHING",
                            (
                                thread_id,
                                current_seq,
                                extra.event_key,
                                extra.type,
                                extra.turn_id,
                                extra.run_id,
                                extra.message_id,
                                extra.tool_call_id,
                                extra.interrupt_id,
                                json.dumps(extra.payload, ensure_ascii=False),
                                extra.created_at or agora,
                            ),
                        )

                # Gravar evento canônico message_created
                current_seq += 1
                msg_event_key = make_message_created_key(new_id)
                msg_payload = {
                    "role": role,
                    "content": msg.text(),
                    # Preserve the tri-state value in the event ledger. The
                    # messages table may normalize legacy NULL values for
                    # reads, but replay/audit must not turn "unknown" into
                    # an asserted non-error.
                    "is_error": msg.is_error,
                }
                await conn.execute(
                    "INSERT INTO thread_events (thread_id, seq, event_key, type, "
                    "turn_id, run_id, message_id, tool_call_id, interrupt_id, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(thread_id, event_key) DO NOTHING",
                    (
                        thread_id,
                        current_seq,
                        msg_event_key,
                        "message_created",
                        turn_id,
                        run_id,
                        new_id,
                        tool_call_id,
                        None,
                        json.dumps(msg_payload, ensure_ascii=False),
                        agora,
                    ),
                )

                # Exclusão atômica de rascunho de run no commit do assistente
                if run_id is not None and role == "assistant" and not msg.tool_calls:
                    await conn.execute(
                        "DELETE FROM run_drafts WHERE thread_id = ? AND run_id = ?",
                        (thread_id, run_id),
                    )

                if run_id is not None and role == "assistant" and not msg.tool_calls:
                    message_terminal_status = terminal_status or (
                        "failed"
                        if msg.is_error and msg.finish_reason == "failed"
                        else "cancelled"
                        if msg.is_error and msg.finish_reason == "cancelled"
                        else "completed"
                    )
                    update_params = (
                        message_terminal_status,
                        agora,
                        agora,
                        msg.text(),
                        run_id,
                    )
                    if terminal_status is not None:
                        await conn.execute(
                            "UPDATE session_runs SET status = ?, updated_at = ?, "
                            "terminal_at = ?, partial_text = COALESCE(?, partial_text) "
                            "WHERE run_id = ? AND status NOT IN "
                            "('completed','failed','cancelled')",
                            update_params,
                        )
                    else:
                        await conn.execute(
                            "UPDATE session_runs SET status = ?, updated_at = ?, "
                            "terminal_at = ?, partial_text = COALESCE(?, partial_text) "
                            "WHERE run_id = ?",
                            update_params,
                        )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
        return int(new_id), current_seq

    async def bump_tool_policy_version(self, scope: str) -> int:
        """Incrementa atomicamente a versão compartilhada da política do escopo."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "INSERT INTO tool_policy_versions (scope, version) VALUES (?, 1) "
                "ON CONFLICT(scope) DO UPDATE SET version = version + 1 "
                "RETURNING version",
                (scope,),
            )
            row = await cur.fetchone()
            await conn.commit()
        if row is None:
            msg = "tool_policy_versions não retornou a versão incrementada"
            raise RuntimeError(msg)
        return int(row[0])

    async def get_tool_policy_versions(self) -> dict[str, int]:
        """Versões persistidas de todos os escopos da política de tools."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute("SELECT scope, version FROM tool_policy_versions")
            rows = await cur.fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    async def append_terminal_outcome(
        self,
        thread_id: str,
        msg: VMessage,
        *,
        parent_message_id: int | None,
        turn_id: str,
        run_id: str,
        status: str,
        detail: str,
    ) -> tuple[int, int]:
        """Commit terminal run state, visible message and ledger event together."""
        extra_events = _terminal_ledger_events(
            thread_id, run_id, turn_id, status, detail
        )
        return await self.append_message_recorded(
            thread_id,
            msg,
            parent_message_id=parent_message_id,
            turn_id=turn_id,
            run_id=run_id,
            extra_events=extra_events,
            terminal_status=status,
            terminal_detail=detail,
        )

    async def attach_edited_files(
        self, thread_id: str, run_id: str, files: list[dict[str, object]]
    ) -> None:
        """Persiste os arquivos finais associados a um run de resposta."""
        await self.setup()
        payload = json.dumps(files, ensure_ascii=False)
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE messages SET edited_files_json = ? "
                "WHERE id = (SELECT MAX(id) FROM messages "
                "WHERE thread_id = ? AND run_id = ? AND role = 'assistant')",
                (payload, thread_id, run_id),
            )
            await conn.commit()

    async def get_edited_files_for_thread(
        self, thread_id: str
    ) -> dict[int, list[dict[str, object]]]:
        """Retorna os payloads de arquivos persistidos por mensagem."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT id, edited_files_json FROM messages "
                "WHERE thread_id = ? AND edited_files_json IS NOT NULL",
                (thread_id,),
            )
            rows = await cur.fetchall()
        result: dict[int, list[dict[str, object]]] = {}
        for message_id, raw in rows:
            try:
                value = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(value, list):
                result[int(message_id)] = [
                    item for item in value if isinstance(item, dict)
                ]
        return result

    async def get_message_id_by_turn_id(
        self, thread_id: str, turn_id: str
    ) -> int | None:
        """Returns the persisted message for an idempotent turn."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT id FROM messages WHERE thread_id = ? AND turn_id = ?",
                (thread_id, turn_id),
            )
            row = await cur.fetchone()
        return int(row[0]) if row is not None else None

    async def get_branch_head_id(self, thread_id: str) -> int | None:
        """`id` da mensagem que é a ponta ativa da branch, ou `None` se a
        thread ainda não tem mensagem nenhuma — usado pelo caller (loop de
        conversa nativo) pra encadear `parent_message_id` ao persistir a
        próxima mensagem, sem precisar reler o histórico inteiro só pra
        descobrir o último `id`."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT id FROM messages WHERE thread_id = ? "
                "AND is_branch_head = 1 ORDER BY id DESC LIMIT 1",
                (thread_id,),
            )
            row = await cur.fetchone()
        return row[0] if row is not None else None

    async def get_history(
        self, thread_id: str, *, up_to_message_id: int | None = None
    ) -> list[VMessage]:
        """Reconstrói a cadeia de mensagens seguindo `parent_message_id` a
        partir da ponta ativa (ou de `up_to_message_id`, pra reler uma
        branch antiga sem apagar o que veio depois) até a raiz — reload/
        resume acontece por reconstrução da persistência, nunca por estado
        mantido só em memória (invariante do loop nativo)."""
        pares = await self.get_history_with_ids(
            thread_id, up_to_message_id=up_to_message_id
        )
        return [msg for _id, msg in pares]

    async def get_history_with_ids(
        self, thread_id: str, *, up_to_message_id: int | None = None
    ) -> list[tuple[int, VMessage]]:
        """Mesma reconstrução de `get_history`, mas devolve `(id, VMessage)` —
        o `id` é o alvo de fork (`set_branch_head`) para "editar e reenviar"/
        "regenerar", exposto pela API REST como `checkpoint_id`.

        Reconstrói a cadeia inteira numa única query recursiva (`WITH
        RECURSIVE`) em vez de uma leitura por mensagem — o loop anterior
        fazia N round-trips sequenciais ao SQLite pra uma thread de N
        mensagens, medido como o principal custo de latência ao abrir
        threads longas. `_CHAIN_DEPTH_CAP` limita a recursão: sem essa
        rede de segurança, um `parent_message_id` corrompido formando um
        ciclo faria a CTE recursar sem fim (o loop antigo detectava ciclo
        via um `set` de ids visitados — a query não tem como fazer o
        equivalente nativamente, então o cap + checagem em Python cobre o
        mesmo caso, ainda que sem apontar o id exato do ciclo)."""
        await self.setup()
        async with self._pool.acquire() as conn:
            if up_to_message_id is not None:
                start_id: int | None = up_to_message_id
            else:
                cur = await conn.execute(
                    "SELECT id FROM messages WHERE thread_id = ? "
                    "AND is_branch_head = 1 ORDER BY id DESC LIMIT 1",
                    (thread_id,),
                )
                row = await cur.fetchone()
                start_id = row[0] if row is not None else None

            if start_id is None:
                return []

            cur = await conn.execute(
                "WITH RECURSIVE chain(id, parent_message_id, role, "
                "content_json, tool_calls_json, tool_call_id, name, "
                "is_error, depth) AS ("
                "  SELECT id, parent_message_id, role, content_json, "
                "    tool_calls_json, tool_call_id, name, is_error, 0"
                "  FROM messages WHERE thread_id = ? AND id = ?"
                "  UNION ALL"
                "  SELECT m.id, m.parent_message_id, m.role, "
                "    m.content_json, m.tool_calls_json, m.tool_call_id, "
                "    m.name, m.is_error, chain.depth + 1"
                "  FROM messages m JOIN chain ON m.id = chain.parent_message_id"
                "  WHERE m.thread_id = ? AND chain.depth < ?"
                ")"
                "SELECT id, parent_message_id, role, content_json, "
                "tool_calls_json, tool_call_id, name, is_error "
                "FROM chain ORDER BY depth DESC",
                (thread_id, start_id, thread_id, _CHAIN_DEPTH_CAP - 1),
            )
            cadeia = list(await cur.fetchall())

        if len(cadeia) >= _CHAIN_DEPTH_CAP:
            erro = (
                f"ciclo detectado em parent_message_id da thread "
                f"'{thread_id}' (cadeia excedeu {_CHAIN_DEPTH_CAP} elos)"
            )
            raise RuntimeError(erro)

        return [(row[0], _row_to_message(row)) for row in cadeia]

    async def set_branch_head(
        self, thread_id: str, message_id: int, *, allow_internal: bool = False
    ) -> None:
        """Marca `message_id` como a ponta ativa da thread — fork explícito
        (editar mensagem/regenerar) sem apagar nenhuma mensagem existente.

        `message_id` precisa pertencer a `thread_id`; caso contrário a
        thread ficaria sem nenhuma ponta ativa (histórico "sumiria")."""
        await self.setup()
        async with self._pool.acquire() as conn:
            if allow_internal:
                query = "SELECT 1 FROM messages WHERE thread_id = ? AND id = ?"
            else:
                query = (
                    "SELECT 1 FROM messages m WHERE m.thread_id = ? AND m.id = ? "
                    "AND NOT EXISTS (SELECT 1 FROM messages d "
                    "WHERE d.thread_id = m.thread_id AND d.parent_message_id = m.id)"
                )
            cur = await conn.execute(query, (thread_id, message_id))
            if await cur.fetchone() is None:
                erro = f"mensagem {message_id} não pertence à thread '{thread_id}'"
                raise ValueError(erro)
            try:
                await conn.execute(
                    "UPDATE messages SET is_branch_head = 0 WHERE thread_id = ?",
                    (thread_id,),
                )
                await conn.execute(
                    "UPDATE messages SET is_branch_head = 1 WHERE thread_id = ? AND id = ?",
                    (thread_id, message_id),
                )
                await conn.execute(
                    "UPDATE sessions SET updated_at = ? WHERE thread_id = ?",
                    (datetime.now(UTC).isoformat(), thread_id),
                )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def list_branch_heads(
        self, thread_id: str, *, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Lista as pontas (folhas) persistidas de uma conversa.

        A ponta ativa continua sendo marcada por ``is_branch_head``. Para
        preservar branches antigas, a enumeração também considera mensagens
        que não são pai de nenhuma outra mensagem.
        """
        await self.setup()
        bounded_limit = max(1, min(limit, 500))
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT m.id, m.created_at, m.is_branch_head, "
                "(SELECT COUNT(*) FROM messages d WHERE d.thread_id = m.thread_id "
                "AND d.parent_message_id = m.id) AS child_count "
                "FROM messages m WHERE m.thread_id = ? "
                "AND NOT EXISTS (SELECT 1 FROM messages d WHERE d.thread_id = m.thread_id "
                "AND d.parent_message_id = m.id) "
                "ORDER BY m.created_at DESC, m.id DESC LIMIT ?",
                (thread_id, bounded_limit),
            )
            rows = await cur.fetchall()
        return [
            {
                "head_message_id": int(row[0]),
                "created_at": str(row[1]),
                "active": bool(row[2]),
                "message_count": len(
                    await self.get_history_with_ids(
                        thread_id, up_to_message_id=int(row[0])
                    )
                ),
            }
            for row in rows
        ]

    async def compare_branches(
        self, thread_id: str, selected_head_id: int
    ) -> dict[str, Any]:
        """Compara a ponta ativa com outra ponta da mesma thread."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT 1 FROM messages m WHERE m.thread_id = ? AND m.id = ? "
                "AND NOT EXISTS (SELECT 1 FROM messages d WHERE d.thread_id = m.thread_id "
                "AND d.parent_message_id = m.id)",
                (thread_id, selected_head_id),
            )
            if await cur.fetchone() is None:
                raise ValueError(f"mensagem {selected_head_id} não é uma ponta")
        selected = await self.get_history_with_ids(
            thread_id, up_to_message_id=selected_head_id
        )
        if not selected or selected[-1][0] != selected_head_id:
            raise ValueError(
                f"mensagem {selected_head_id} não pertence à thread '{thread_id}'"
            )
        active_head = await self.get_branch_head_id(thread_id)
        active = (
            await self.get_history_with_ids(thread_id, up_to_message_id=active_head)
            if active_head is not None
            else []
        )
        active_ids = [item[0] for item in active]
        selected_ids = [item[0] for item in selected]
        common = 0
        for left, right in zip(active_ids, selected_ids, strict=False):
            if left != right:
                break
            common += 1
        result = {
            "active_head_message_id": active_head,
            "selected_head_message_id": selected_head_id,
            "common_message_ids": active_ids[:common],
            "active_divergent_message_ids": active_ids[common:],
            "selected_divergent_message_ids": selected_ids[common:],
        }
        max_items = 2000
        if len(active_ids) + len(selected_ids) > max_items:
            raise ValueError("comparação de branches excede o limite permitido")
        return result

    async def get_session(
        self, thread_id: str, *, user_id: str | None = None
    ) -> dict[str, Any] | None:
        """Metadados de posse de uma sessão — fonte de verdade sobre a
        EXISTÊNCIA e o DONO (`user_id`) de uma thread no motor nativo.

        Quando `user_id` é passado, devolve `None` também quando a thread
        pertence a outro usuário — não distingue "não existe" de "não é
        sua" pro caller, evitando vazamento de existência em endpoints
        protegidos (ex.: `GetThread`/`UpdateThread` de outra pessoa)."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT thread_id, user_id, workspace_id, parent_thread_id, mode, "
                "permission_mode, created_at, updated_at FROM sessions "
                "WHERE thread_id = ?",
                (thread_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        session = {
            "thread_id": row[0],
            "user_id": row[1],
            "workspace_id": row[2],
            "parent_thread_id": row[3],
            "mode": row[4],
            "permission_mode": row[5],
            "created_at": row[6],
            "updated_at": row[7],
        }
        if user_id is not None and session["user_id"] != user_id:
            return None
        return session

    async def foreign_thread_ids(self, thread_ids: list[str], user_id: str) -> set[str]:
        """Subconjunto de `thread_ids` registrado em `sessions` com um dono
        DIFERENTE de `user_id` — usado por `ListThreads` pra nunca vazar uma
        thread de outro usuário, mesmo quando outra fonte de metadados (ex.:
        cache de UI) ainda a lista. Threads sem registro nenhum em `sessions`
        (legado, criadas antes da posse ser rastreada aqui) não entram no
        resultado — ausência de registro não é o mesmo que posse alheia."""
        if not thread_ids:
            return set()
        await self.setup()
        placeholders = ",".join("?" for _ in thread_ids)
        query = f"SELECT thread_id FROM sessions WHERE user_id != ? AND thread_id IN ({placeholders})"  # noqa: S608  # nosec B608
        async with self._pool.acquire() as conn:
            cur = await conn.execute(query, (user_id, *thread_ids))
            rows = await cur.fetchall()
        return {r[0] for r in rows}

    async def list_all_sessions(self) -> list[SessionSummary]:
        """Todas as threads registradas em `sessions` (fonte de verdade),
        com a contagem real de mensagens de cada uma — usado pela
        reconciliação de `vectora_sessions` (metadados de UI), que precisa
        comparar as duas tabelas pra achar threads reais ausentes/
        desatualizadas na tabela que a sidebar lê."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT s.thread_id, s.user_id, s.workspace_id, s.mode, "
                "s.created_at, s.updated_at, "
                "(SELECT COUNT(*) FROM messages m WHERE m.thread_id = s.thread_id) "
                "AS message_count FROM sessions s"
            )
            rows = await cur.fetchall()
        return [
            {
                "thread_id": r[0],
                "user_id": r[1],
                "workspace_id": r[2],
                "mode": r[3],
                "created_at": r[4],
                "updated_at": r[5],
                "message_count": r[6],
            }
            for r in rows
        ]

    async def count_assistant_messages(self, thread_id: str) -> int | None:
        """Conta somente respostas persistidas do assistente.

        A tabela ``messages`` também guarda mensagens de usuário, tool calls e
        resultados internos. Esses registros são necessários para reconstruir
        o histórico, mas não são notificações novas para a sidebar.
        """
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(role = 'assistant'), 0) "
                "FROM messages WHERE thread_id = ?",
                (thread_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        # A session with no persisted messages is used by a few legacy/import
        # paths that only maintain the UI counter. Preserve that fallback; a
        # real user-only conversation has rows and correctly yields zero.
        return int(row[1]) if int(row[0]) else None

    async def count_assistant_messages_after(
        self, thread_id: str, message_id: int
    ) -> int | None:
        """Conta respostas assistant posteriores a um cursor confirmado."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM messages WHERE thread_id = ? "
                "AND role = 'assistant' AND id > ?",
                (thread_id, message_id),
            )
            row = await cur.fetchone()
        return int(row[0]) if row is not None else None

    async def latest_assistant_message_id(
        self, thread_id: str, run_id: str | None = None
    ) -> int | None:
        """Retorna o cursor monotônico da última resposta assistant.

        Se `run_id` for informado, restringe a busca estritamente às mensagens
        assistant gravadas por esse run específico, retornando `None` se o run
        não tiver gravado nenhuma resposta assistant.
        """
        await self.setup()
        async with self._pool.acquire() as conn:
            if run_id:
                cur = await conn.execute(
                    "SELECT MAX(id) FROM messages "
                    "WHERE thread_id = ? AND run_id = ? AND role = 'assistant' "
                    "AND (tool_calls_json IS NULL OR tool_calls_json = '[]')",
                    (thread_id, run_id),
                )
                row = await cur.fetchone()
                return int(row[0]) if (row is not None and row[0] is not None) else None

            cur = await conn.execute(
                "SELECT COALESCE(MAX(id), 0) FROM messages "
                "WHERE thread_id = ? AND role = 'assistant' "
                "AND (tool_calls_json IS NULL OR tool_calls_json = '[]')",
                (thread_id,),
            )
            row = await cur.fetchone()
        return int(row[0] or 0) if row is not None else 0

    async def get_event_seq_by_message_id(
        self, thread_id: str, message_id: int | str
    ) -> int | None:
        """Localiza o seq do evento message_created correspondente a message_id."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT seq FROM thread_events WHERE thread_id = ? AND type = 'message_created' "
                "AND (message_id = ? OR message_id = CAST(? AS INTEGER)) ORDER BY seq DESC LIMIT 1",
                (thread_id, message_id, message_id),
            )
            row = await cur.fetchone()
            return int(row[0]) if row and row[0] is not None else None

    async def list_active_user_ids(self, since_iso: str) -> list[str]:
        """`user_id` distintos com pelo menos uma sessão atualizada desde
        `since_iso` (string ISO comparável lexicograficamente com
        `updated_at`) — usado pelo scheduler de consolidação de memória
        pra saber quem teve atividade recente, sem varrer todas as threads."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT DISTINCT user_id FROM sessions WHERE updated_at >= ?",
                (since_iso,),
            )
            rows = await cur.fetchall()
        return [r[0] for r in rows if r[0]]

    async def get_pending_approval(self, thread_id: str) -> dict[str, Any] | None:
        """Executa a operação get pending approval."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT interrupt_id, tool_name, tool_call_id, run_id, args_json, "
                "reasoning, options_json, priority, expires_at, created_at "
                "FROM pending_approvals WHERE thread_id = ?",
                (thread_id,),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        (
            interrupt_id,
            tool_name,
            tool_call_id,
            run_id,
            args_json,
            reasoning,
            options_json,
            priority,
            expires_at,
            created_at,
        ) = row
        if expires_at and expires_at <= _now():
            agora = _now()
            async with self._pool.acquire() as conn:
                await conn.execute("BEGIN IMMEDIATE")
                try:
                    cur_del = await conn.execute(
                        "DELETE FROM pending_approvals "
                        "WHERE thread_id = ? AND interrupt_id = ? "
                        "AND expires_at IS NOT NULL AND expires_at <= ?",
                        (thread_id, interrupt_id, agora),
                    )
                    if cur_del.rowcount <= 0:
                        await conn.rollback()
                        return None
                    cur_max = await conn.execute(
                        "SELECT COALESCE(MAX(seq), 0) FROM thread_events WHERE thread_id = ?",
                        (thread_id,),
                    )
                    row_max = await cur_max.fetchone()
                    current_seq = (int(row_max[0]) if row_max else 0) + 1
                    resolved_key = make_approval_resolved_key(interrupt_id)
                    payload = {
                        "tool_name": tool_name,
                        "tool_call_id": tool_call_id,
                        "interrupt_id": interrupt_id,
                        "expired": True,
                    }
                    await conn.execute(
                        "INSERT INTO thread_events (thread_id, seq, event_key, type, "
                        "turn_id, run_id, message_id, tool_call_id, interrupt_id, payload_json, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                        "ON CONFLICT(thread_id, event_key) DO NOTHING",
                        (
                            thread_id,
                            current_seq,
                            resolved_key,
                            "approval_resolved",
                            None,
                            None,
                            None,
                            tool_call_id,
                            interrupt_id,
                            json.dumps(payload, ensure_ascii=False),
                            agora,
                        ),
                    )
                    await conn.commit()
                except Exception:
                    await conn.rollback()
                    logger.exception(
                        "session_store: falha ao expirar pending approval para thread %s",
                        thread_id,
                    )
            return None
        return {
            "interrupt_id": interrupt_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "run_id": run_id,
            "args": json.loads(args_json),
            "reasoning": reasoning,
            "options": json.loads(options_json or "[]"),
            "priority": int(priority),
            "expires_at": expires_at,
            "created_at": created_at,
        }

    async def claim_pending_approval(
        self, thread_id: str, *, interrupt_id: str
    ) -> dict[str, Any] | None:
        """Atomically consume a matching pending approval for one resumer,
        recording an approval_resolved event in the ledger on the same transaction."""
        await self.setup()
        agora = _now()
        async with self._pool.acquire() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                cur = await conn.execute(
                    "DELETE FROM pending_approvals WHERE thread_id = ? AND interrupt_id = ? "
                    "RETURNING interrupt_id, tool_name, tool_call_id, run_id, args_json, reasoning, "
                    "options_json, priority, expires_at, created_at",
                    (thread_id, interrupt_id),
                )
                row = await cur.fetchone()
                if row is None:
                    await conn.rollback()
                    return None

                (
                    claimed_id,
                    tool_name,
                    tool_call_id,
                    run_id,
                    args_json,
                    reasoning,
                    options_json,
                    priority,
                    expires_at,
                    created_at,
                ) = row
                is_expired = bool(expires_at and expires_at <= _now())

                # Obter próximo seq do ledger
                cur_max = await conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM thread_events WHERE thread_id = ?",
                    (thread_id,),
                )
                row_max = await cur_max.fetchone()
                current_seq = (int(row_max[0]) if row_max else 0) + 1

                resolved_key = make_approval_resolved_key(claimed_id)
                payload = {
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "interrupt_id": claimed_id,
                    "expired": is_expired,
                }
                await conn.execute(
                    "INSERT INTO thread_events (thread_id, seq, event_key, type, "
                    "turn_id, run_id, message_id, tool_call_id, interrupt_id, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(thread_id, event_key) DO NOTHING",
                    (
                        thread_id,
                        current_seq,
                        resolved_key,
                        "approval_resolved",
                        None,
                        None,
                        None,
                        tool_call_id,
                        claimed_id,
                        json.dumps(payload, ensure_ascii=False),
                        agora,
                    ),
                )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

        if is_expired:
            return None

        return {
            "interrupt_id": claimed_id,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "run_id": run_id,
            "args": json.loads(args_json),
            "reasoning": reasoning,
            "options": json.loads(options_json or "[]"),
            "priority": int(priority),
            "expires_at": expires_at,
            "created_at": created_at,
        }

    async def put_pending_approval(
        self,
        thread_id: str,
        *,
        interrupt_id: str,
        tool_name: str,
        tool_call_id: str,
        run_id: str | None = None,
        args: dict[str, Any],
        reasoning: str | None = None,
        options: list[ToolOption] | None = None,
        priority: int = 0,
        expires_at: str | None = None,
    ) -> None:
        """Persiste IMEDIATA e SINCRONAMENTE a aprovação pendente e grava
        o evento approval_requested correspondente no ledger."""
        await self.setup()
        agora = _now()
        async with self._pool.acquire() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                await conn.execute(
                    "INSERT OR REPLACE INTO pending_approvals (thread_id, "
                    "interrupt_id, tool_name, tool_call_id, run_id, args_json, reasoning, options_json, "
                    "priority, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        thread_id,
                        interrupt_id,
                        tool_name,
                        tool_call_id,
                        run_id,
                        json.dumps(args, ensure_ascii=False),
                        reasoning,
                        json.dumps(options or [], ensure_ascii=False),
                        priority,
                        expires_at,
                        agora,
                    ),
                )
                # Obter próximo seq do ledger
                cur_max = await conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM thread_events WHERE thread_id = ?",
                    (thread_id,),
                )
                row_max = await cur_max.fetchone()
                current_seq = (int(row_max[0]) if row_max else 0) + 1

                req_key = make_approval_requested_key(interrupt_id)
                payload = {
                    "tool_name": tool_name,
                    "tool_call_id": tool_call_id,
                    "run_id": run_id,
                    "args": args,
                    "reasoning": reasoning,
                    "priority": priority,
                }
                await conn.execute(
                    "INSERT INTO thread_events (thread_id, seq, event_key, type, "
                    "turn_id, run_id, message_id, tool_call_id, interrupt_id, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(thread_id, event_key) DO NOTHING",
                    (
                        thread_id,
                        current_seq,
                        req_key,
                        "approval_requested",
                        None,
                        run_id,
                        None,
                        tool_call_id,
                        interrupt_id,
                        json.dumps(payload, ensure_ascii=False),
                        agora,
                    ),
                )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def clear_pending_approval(self, thread_id: str) -> None:
        """Executa a operação clear pending approval."""
        await self.setup()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM pending_approvals WHERE thread_id = ?", (thread_id,)
            )
            await conn.commit()

    async def record_approval_decision(
        self,
        thread_id: str,
        *,
        interrupt_id: str,
        tool_name: str,
        decision: str,
        selection: str | None,
        decided_by: str | None,
        options: list[ToolOption],
        priority: int,
        expires_at: str | None,
    ) -> None:
        """Persist the final HITL decision before clearing its pending state."""
        await self.setup()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO approval_decisions (thread_id, interrupt_id, tool_name, "
                "decision, selection, decided_by, decided_at, options_json, priority, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    thread_id,
                    interrupt_id,
                    tool_name,
                    decision,
                    selection,
                    decided_by,
                    _now(),
                    json.dumps(options, ensure_ascii=False),
                    priority,
                    expires_at,
                ),
            )
            await conn.commit()

    async def delete_session(self, thread_id: str) -> None:
        """Apaga `thread_id` e tudo que referencia ela (`messages`,
        `pending_approvals`, `thread_events`, `thread_event_cursors`, `run_drafts`)."""
        await self.setup()
        async with self._pool.acquire() as conn:
            try:
                await conn.execute(
                    "DELETE FROM pending_approvals WHERE thread_id = ?", (thread_id,)
                )
                await conn.execute(
                    "DELETE FROM messages WHERE thread_id = ?", (thread_id,)
                )
                await conn.execute(
                    "DELETE FROM session_runs WHERE thread_id = ?", (thread_id,)
                )
                await conn.execute(
                    "DELETE FROM thread_events WHERE thread_id = ?", (thread_id,)
                )
                await conn.execute(
                    "DELETE FROM thread_event_cursors WHERE thread_id = ?", (thread_id,)
                )
                await conn.execute(
                    "DELETE FROM run_drafts WHERE thread_id = ?", (thread_id,)
                )
                await conn.execute(
                    "DELETE FROM agent_runs WHERE thread_id = ?", (thread_id,)
                )
                await conn.execute(
                    "DELETE FROM context_projections WHERE thread_id = ?", (thread_id,)
                )
                await conn.execute(
                    "DELETE FROM context_checkpoints WHERE thread_id = ?", (thread_id,)
                )
                await conn.execute(
                    "DELETE FROM ledger_backfill WHERE thread_id = ?", (thread_id,)
                )
                await conn.execute(
                    "DELETE FROM sessions WHERE thread_id = ?", (thread_id,)
                )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    # -----------------------------------------------------------------------
    # Ledger de Eventos, Cursores de Leitura e Rascunhos
    # -----------------------------------------------------------------------

    async def append_event(
        self, thread_id: str, draft: ThreadLedgerEvent
    ) -> ThreadLedgerEvent:
        """Gera seq monótono e insere o evento no ledger.
        Em caso de conflito de event_key, retorna o evento existente sem erro."""
        await self.setup()
        await ensure_thread_backfilled(self._pool, thread_id)
        agora = _now()
        created_at = draft.created_at or agora
        payload_str = json.dumps(draft.payload, ensure_ascii=False)
        async with self._pool.acquire() as conn:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                # Verificar se event_key já existe
                cur_existing = await conn.execute(
                    "SELECT thread_id, seq, event_key, type, turn_id, run_id, "
                    "message_id, tool_call_id, interrupt_id, payload_json, created_at "
                    "FROM thread_events WHERE thread_id = ? AND event_key = ?",
                    (thread_id, draft.event_key),
                )
                row = await cur_existing.fetchone()
                if row is not None:
                    await conn.rollback()
                    return ThreadLedgerEvent(
                        thread_id=row[0],
                        seq=row[1],
                        event_key=row[2],
                        type=row[3],
                        turn_id=row[4],
                        run_id=row[5],
                        message_id=row[6],
                        tool_call_id=row[7],
                        interrupt_id=row[8],
                        payload=json.loads(row[9] or "{}"),
                        created_at=row[10],
                    )

                cur_max = await conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM thread_events WHERE thread_id = ?",
                    (thread_id,),
                )
                row_max = await cur_max.fetchone()
                next_seq = (int(row_max[0]) if row_max else 0) + 1

                await conn.execute(
                    "INSERT INTO thread_events (thread_id, seq, event_key, type, "
                    "turn_id, run_id, message_id, tool_call_id, interrupt_id, payload_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        thread_id,
                        next_seq,
                        draft.event_key,
                        draft.type,
                        draft.turn_id,
                        draft.run_id,
                        draft.message_id,
                        draft.tool_call_id,
                        draft.interrupt_id,
                        payload_str,
                        created_at,
                    ),
                )
                await conn.commit()
                return ThreadLedgerEvent(
                    thread_id=thread_id,
                    seq=next_seq,
                    event_key=draft.event_key,
                    type=draft.type,
                    turn_id=draft.turn_id,
                    run_id=draft.run_id,
                    message_id=draft.message_id,
                    tool_call_id=draft.tool_call_id,
                    interrupt_id=draft.interrupt_id,
                    payload=draft.payload,
                    created_at=created_at,
                )
            except Exception:
                await conn.rollback()
                raise

    async def append_ledger_event(
        self,
        thread_id: str,
        *,
        event_key: str,
        event_type: LedgerEventType | str,
        run_id: str | None = None,
        turn_id: str | None = None,
        message_id: int | None = None,
        tool_call_id: str | None = None,
        interrupt_id: str | None = None,
        payload: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> ThreadLedgerEvent:
        """Helper para registrar eventos no ledger usando argumentos nomeados."""
        event = ThreadLedgerEvent(
            thread_id=thread_id,
            event_key=event_key,
            type=cast("LedgerEventTypeLiteral", event_type),
            run_id=run_id,
            turn_id=turn_id,
            message_id=message_id,
            tool_call_id=tool_call_id,
            interrupt_id=interrupt_id,
            payload=payload or {},
            created_at=created_at or _now(),
        )
        return await self.append_event(thread_id, event)

    async def list_events(
        self, thread_id: str, *, after_seq: int = 0, limit: int = 500
    ) -> list[ThreadLedgerEvent]:
        """Lista eventos do ledger ordenados por seq. Para message_created, junta o texto da mensagem se faltar no payload."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT e.thread_id, e.seq, e.event_key, e.type, e.turn_id, e.run_id, "
                "e.message_id, e.tool_call_id, e.interrupt_id, e.payload_json, e.created_at, "
                "m.role, m.content_json, m.is_error "
                "FROM thread_events e "
                "LEFT JOIN messages m ON e.message_id = m.id "
                "WHERE e.thread_id = ? AND e.seq > ? "
                "ORDER BY e.seq ASC LIMIT ?",
                (thread_id, after_seq, limit),
            )
            rows = await cur.fetchall()

        events: list[ThreadLedgerEvent] = []
        for r in rows:
            payload = json.loads(r[9] or "{}")
            if r[3] == "message_created" and r[11] is not None:
                if not payload.get("role"):
                    payload["role"] = r[11]
                if "content" not in payload and r[12]:
                    try:
                        blocks = json.loads(r[12])
                        text_val = "".join(
                            b.get("text", "") for b in blocks if isinstance(b, dict)
                        )
                        payload["content"] = text_val
                    except Exception:
                        pass
                if "is_error" not in payload and r[13] is not None:
                    payload["is_error"] = bool(r[13])

            events.append(
                ThreadLedgerEvent(
                    thread_id=r[0],
                    seq=r[1],
                    event_key=r[2],
                    type=r[3],
                    turn_id=r[4],
                    run_id=r[5],
                    message_id=r[6],
                    tool_call_id=r[7],
                    interrupt_id=r[8],
                    payload=payload,
                    created_at=r[10],
                )
            )
        return events

    async def mark_events_seen(
        self, thread_id: str, user_id: str, device_id: str, seen_seq: int
    ) -> dict[str, int]:
        """Atualiza o cursor com MAX(existing, seen_seq), limitado ao MAX(seq) da thread."""
        await self.setup()
        now = _now()
        async with self._pool.acquire() as conn:
            # O cursor e a contagem precisam ser observados no mesmo snapshot.
            # Caso contrário, outro dispositivo pode confirmar a leitura entre
            # as duas consultas e devolver um par impossível (read_seq antigo
            # com unread_count calculado a partir de um cursor novo).
            await conn.execute("BEGIN IMMEDIATE")
            try:
                cur_max = await conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) FROM thread_events WHERE thread_id = ?",
                    (thread_id,),
                )
                row_max = await cur_max.fetchone()
                max_seq = int(row_max[0]) if row_max else 0
                clamped_seq = max(0, min(seen_seq, max_seq))

                await conn.execute(
                    "INSERT INTO thread_event_cursors (thread_id, user_id, device_id, last_seen_seq, updated_at) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(thread_id, user_id, device_id) DO UPDATE SET "
                    "last_seen_seq = MAX(thread_event_cursors.last_seen_seq, excluded.last_seen_seq), "
                    "updated_at = excluded.updated_at",
                    (thread_id, user_id, device_id, clamped_seq, now),
                )
                cursor = await conn.execute(
                    "SELECT COALESCE(MAX(last_seen_seq), 0) "
                    "FROM thread_event_cursors WHERE thread_id = ? AND user_id = ?",
                    (thread_id, user_id),
                )
                cursor_row = await cursor.fetchone()
                read_seq = int(cursor_row[0]) if cursor_row else 0
                unread = await conn.execute(
                    "SELECT COALESCE(SUM(CASE WHEN seq > ? AND "
                    "(type IN ('approval_requested','run_failed') OR "
                    "(type = 'message_created' AND "
                    "json_extract(payload_json, '$.role') = 'assistant')) "
                    "THEN 1 ELSE 0 END), 0) FROM thread_events WHERE thread_id = ?",
                    (read_seq, thread_id),
                )
                unread_row = await unread.fetchone()
                unread_count = int(unread_row[0]) if unread_row else 0
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
        return {"read_seq": read_seq, "unread_count": unread_count}

    async def get_thread_summaries(
        self, thread_ids: list[str], *, user_id: str
    ) -> list[ThreadSummary]:
        """Retorna resumos derivados em lote para uma lista de thread_ids."""
        if not thread_ids:
            return []
        await self.setup()
        placeholders = ",".join("?" for _ in thread_ids)

        async with self._pool.acquire() as conn:
            # 1. Obter maior cursor do usuário entre dispositivos para cada thread
            cur_cursors = await conn.execute(
                f"SELECT thread_id, COALESCE(MAX(last_seen_seq), 0) FROM thread_event_cursors "  # noqa: S608  # nosec B608
                f"WHERE user_id = ? AND thread_id IN ({placeholders}) GROUP BY thread_id",
                (user_id, *thread_ids),
            )
            cursors: dict[str, int] = {
                str(r[0]): int(r[1]) for r in await cur_cursors.fetchall()
            }

            # 2. Obter max_seq por thread
            cur_max = await conn.execute(
                f"SELECT thread_id, COALESCE(MAX(seq), 0) FROM thread_events "  # noqa: S608  # nosec B608
                f"WHERE thread_id IN ({placeholders}) GROUP BY thread_id",
                tuple(thread_ids),
            )
            max_seqs: dict[str, int] = {
                str(r[0]): int(r[1]) for r in await cur_max.fetchall()
            }

            status_types = (
                "run_completed",
                "agent_turn_finished",
                "run_failed",
                "run_cancelled",
                "approval_resolved",
                "approval_requested",
                "agent_turn_started",
            )
            status_placeholders = ",".join("?" for _ in status_types)
            cur_status = await conn.execute(
                "SELECT thread_id, type, payload_json FROM ("  # nosec B608  # noqa: S608
                " SELECT thread_id, type, payload_json, "
                " ROW_NUMBER() OVER (PARTITION BY thread_id ORDER BY seq DESC) AS rn "
                f" FROM thread_events WHERE thread_id IN ({placeholders}) "
                f" AND type IN ({status_placeholders})"
                ") latest WHERE rn = 1",
                (*thread_ids, *status_types),
            )
            status_rows = await cur_status.fetchall()

            notifiable_types = tuple(
                event_type
                for event_type in sorted(NOTIFIABLE_EVENT_TYPES)
                if event_type != "message_created"
            )
            notifiable_placeholders = ",".join("?" for _ in notifiable_types)
            cur_unread = await conn.execute(
                "SELECT e.thread_id, COUNT(*) FROM thread_events e "  # nosec B608  # noqa: S608
                "LEFT JOIN (SELECT thread_id, MAX(last_seen_seq) AS read_seq "
                "FROM thread_event_cursors WHERE user_id = ? "
                f"AND thread_id IN ({placeholders}) GROUP BY thread_id) c "
                "ON c.thread_id = e.thread_id "
                f"WHERE e.thread_id IN ({placeholders}) "
                "AND e.seq > COALESCE(c.read_seq, 0) AND ("
                f"e.type IN ({notifiable_placeholders}) OR "
                "(e.type = 'message_created' "
                "AND json_extract(e.payload_json, '$.role') = 'assistant')) "
                "GROUP BY e.thread_id",
                (user_id, *thread_ids, *thread_ids, *notifiable_types),
            )
            unread_counts = {
                str(row[0]): int(row[1]) for row in await cur_unread.fetchall()
            }

            # 4. Obter última mensagem persistida
            cur_last_msgs = await conn.execute(
                f"SELECT m.thread_id, m.id, m.role, m.content_json, m.created_at "  # noqa: S608  # nosec B608
                f"FROM messages m "
                f"INNER JOIN ("
                f"  SELECT thread_id, MAX(id) as max_id FROM messages "
                f"  WHERE thread_id IN ({placeholders}) GROUP BY thread_id"
                f") latest ON m.thread_id = latest.thread_id AND m.id = latest.max_id",
                tuple(thread_ids),
            )
            last_msgs = {}
            for row_msg in await cur_last_msgs.fetchall():
                t_id, m_id, role, c_json, c_at = row_msg
                try:
                    blocks = json.loads(c_json or "[]")
                    preview = "".join(
                        b.get("text", "") for b in blocks if isinstance(b, dict)
                    )
                except Exception:
                    preview = ""
                last_msgs[t_id] = ThreadLastMessage(
                    role=role,
                    preview=preview,
                    message_id=m_id,
                    created_at=c_at,
                )

        status_by_thread = {str(row[0]): (str(row[1]), row[2]) for row in status_rows}

        summaries: list[ThreadSummary] = []
        for tid in thread_ids:
            read_seq = int(cursors.get(tid, 0))
            last_seq = int(max_seqs.get(tid, 0))
            status = "idle"
            status_row = status_by_thread.get(tid)
            if status_row:
                event_type, payload_json = status_row
                if event_type == "run_failed":
                    status = "failed"
                elif event_type == "run_cancelled":
                    status = "idle"
                elif event_type == "approval_requested":
                    status = "awaiting_approval"
                elif event_type == "agent_turn_started":
                    status = "running"
                elif event_type == "approval_resolved":
                    payload = json.loads(payload_json or "{}")
                    status = "idle" if payload.get("expired", False) else "running"

            unread_count = unread_counts.get(tid, 0)

            summaries.append(
                ThreadSummary(
                    thread_id=tid,
                    last_seq=last_seq,
                    last_message=last_msgs.get(tid),
                    status=status,
                    read_seq=read_seq,
                    unread_count=unread_count,
                )
            )
        return summaries

    async def upsert_run_draft(
        self,
        thread_id: str,
        run_id: str,
        *,
        content: str = "",
        tool_calls_json: str | None = None,
    ) -> None:
        """Salva rascunho intermediário de um run."""
        await self.setup()
        now = _now()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO run_drafts (thread_id, run_id, content, tool_calls_json, updated_at) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(thread_id, run_id) DO UPDATE SET "
                "content = excluded.content, tool_calls_json = excluded.tool_calls_json, "
                "updated_at = excluded.updated_at",
                (thread_id, run_id, content, tool_calls_json, now),
            )
            await conn.commit()

    async def get_run_draft(self, thread_id: str, run_id: str) -> RunDraft | None:
        """Recupera o rascunho de um run."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT thread_id, run_id, content, tool_calls_json, updated_at "
                "FROM run_drafts WHERE thread_id = ? AND run_id = ?",
                (thread_id, run_id),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return RunDraft(
            thread_id=row[0],
            run_id=row[1],
            content=row[2],
            tool_calls_json=row[3],
            updated_at=row[4],
        )

    async def delete_run_draft(self, thread_id: str, run_id: str) -> None:
        """Apaga o rascunho de um run concluído ou descartado."""
        await self.setup()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM run_drafts WHERE thread_id = ? AND run_id = ?",
                (thread_id, run_id),
            )
            await conn.commit()

    async def find_orphan_runs(self) -> list[tuple[str, str]]:
        """Identifica runs com agent_turn_started sem término (run_completed, run_failed ou agent_turn_finished)."""
        await self.setup()
        async with self._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT DISTINCT e1.thread_id, e1.run_id FROM thread_events e1 "
                "WHERE e1.type = 'agent_turn_started' AND e1.run_id IS NOT NULL "
                "AND NOT EXISTS ("
                "  SELECT 1 FROM thread_events e2 "
                "  WHERE e2.thread_id = e1.thread_id AND e2.run_id = e1.run_id "
                "  AND e2.type IN ('run_completed', 'run_failed', 'run_cancelled', 'agent_turn_finished')"
                ")"
            )
            rows = await cur.fetchall()
        return [(r[0], r[1]) for r in rows]
