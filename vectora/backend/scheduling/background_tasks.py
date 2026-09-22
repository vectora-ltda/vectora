"""Tarefas em segundo plano por session — rotina (cron), heartbreak (evento) e manual.

Uma *background task* pertence a uma session do chat (`session_id` = thread_id) e roda
o agente Vectora autonomamente. Cada execução cria uma thread própria (`run_thread_id`),
registrada em `vectora_sessions` para aparecer na sidebar, e uma linha em
`vectora_background_runs` com o resultado.

Triggers:
    interval — cron (`trigger_config={"cron_expr": "..."}`), avaliado pelo BackgroundScheduler.
    webhook  — disparado por `dispatch_webhook_event` quando um evento externo casa o filtro
               (`trigger_config={"provider": "github", "events": ["push", ...], "repo"?: ...}`).
    manual   — disparado sob demanda via `run_task(task, "manual")`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo
from typing import Any, TypedDict, cast
from uuid import uuid4
from zoneinfo import ZoneInfo

from croniter import croniter

from backend.engine.conversation_loop import (
    LoopConfig,
    resume_conversation,
    run_conversation,
)
from backend.engine.goal_mode import resume_goal, run_goal
from backend.engine.hitl import should_require_approval
from backend.llm.fallback_chat_client import FallbackChatClient
from backend.rbac.subscription import require_pro
from backend.vtypes.context import ctx_from_config
from backend.vtypes.message import MessageRole, text_message

logger = logging.getLogger(__name__)

VALID_KINDS = {"routine", "heartbreak", "subagent", "goal"}
#: "once" — execução única numa hora futura (``next_run_at`` explícito, sem
#: ``cron_expr`` recorrente) — usado por ``schedule_subagent_task``.
VALID_TRIGGERS = {"interval", "once", "webhook", "manual", "subagent"}
VALID_PRIORITIES = {"low", "normal", "high", "urgent"}

#: Limite de tarefas agendadas (não-``manual``) por workspace — evita um
#: workspace acumular centenas de tarefas `interval`/`webhook`/`once` sem
#: controle. Tarefas `manual` (só disparam sob demanda, nunca autônomas)
#: nunca contam pra essa quota.
MAX_SCHEDULED_TASKS_PER_WORKSPACE = 50

#: Tolerância pra disparo atrasado de tarefa recorrente. Além disso, o
#: disparo é pulado e a tarefa reagenda pro próximo horário — evita que
#: voltar de um downtime longo dispare de uma vez tudo que "venceu"
#: enquanto o processo estava parado.
CATCH_UP_GRACE = timedelta(minutes=10)


# ---------------------------------------------------------------------------
# Modelo
# ---------------------------------------------------------------------------


@dataclass
class BackgroundTask:
    """Tarefa em segundo plano vinculada a uma session do chat."""

    id: str
    session_id: str
    user_id: str
    kind: str
    name: str
    instruction: str
    trigger_type: str
    trigger_config: dict[str, Any] = field(default_factory=dict)
    workspace_id: str | None = None
    enabled: bool = True
    last_run_at: str | None = None
    next_run_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    #: Estado do Kanban — lido/escrito via `backend.scheduling.kanban`,
    #: nunca por SQL direto aqui. Default `"todo"` casa com o `DEFAULT` do
    #: schema; `create_task` já promove pra `"ready"` antes de devolver.
    status: str = "todo"
    block_kind: str | None = None
    block_reason: str | None = None
    #: Perfil de agente customizado — `None` = comportamento padrão do
    #: orchestrator, sem mudança de instrução/modelo/budget.
    agent_profile_id: str | None = None
    #: Sinal visual do card no Kanban — "low" | "normal" | "high" | "urgent".
    #: Não afeta ordem real de claim (`claim_task` é FIFO por status).
    priority: str = "normal"
    #: Coluna já existe no SQL desde `claim_task` — só nunca era lida de
    #: volta. `None` quando não há claim ativo (task não `running`, ou já
    #: finalizada).
    claim_expires_at: str | None = None
    #: `None` = task sem board associado (multi-board opcional).
    #: `session_id` continua sendo o contexto de execução — board é só
    #: agrupamento nomeado por cima.
    board_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "kind": self.kind,
            "name": self.name,
            "instruction": self.instruction,
            "trigger_type": self.trigger_type,
            "trigger_config": self.trigger_config,
            "workspace_id": self.workspace_id,
            "enabled": self.enabled,
            "last_run_at": self.last_run_at,
            "next_run_at": self.next_run_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "status": self.status,
            "block_kind": self.block_kind,
            "block_reason": self.block_reason,
            "agent_profile_id": self.agent_profile_id,
            "priority": self.priority,
            "claim_expires_at": self.claim_expires_at,
            "board_id": self.board_id,
        }


class BackgroundRunRow(TypedDict):
    """Linha de run exposta pelos endpoints de histórico."""

    id: str
    task_id: str
    session_id: str
    run_thread_id: str | None
    trigger_source: str
    status: str
    summary: str | None
    started_at: str
    finished_at: str | None


def _row_to_task(row: dict[str, Any]) -> BackgroundTask:
    cfg_raw = row.get("trigger_config") or "{}"
    try:
        cfg = json.loads(cfg_raw) if isinstance(cfg_raw, str) else dict(cfg_raw)
    except (ValueError, TypeError):
        cfg = {}
    return BackgroundTask(
        id=row["id"],
        session_id=row["session_id"],
        user_id=str(row["user_id"]),
        kind=row["kind"],
        name=row["name"],
        instruction=row["instruction"],
        trigger_type=row["trigger_type"],
        trigger_config=cfg,
        workspace_id=row.get("workspace_id"),
        enabled=bool(row.get("enabled", 1)),
        last_run_at=row.get("last_run_at"),
        next_run_at=row.get("next_run_at"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
        status=row.get("status") or "todo",
        block_kind=row.get("block_kind"),
        block_reason=row.get("block_reason"),
        agent_profile_id=row.get("agent_profile_id"),
        priority=row.get("priority") or "normal",
        claim_expires_at=row.get("claim_expires_at"),
        board_id=row.get("board_id"),
    )


# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------


async def _get_db() -> Any:
    """Conexão aiosqlite com row_factory de dict (injetável em testes)."""
    import aiosqlite

    from backend.settings import settings

    db_path = settings.db_dsn or ":memory:"
    conn: Any = await aiosqlite.connect(db_path)
    conn.row_factory = lambda c, r: dict(
        zip([col[0] for col in c.description], r, strict=False)
    )
    return conn


# ---------------------------------------------------------------------------
# Validação / agendamento
# ---------------------------------------------------------------------------


def _validate(
    kind: str,
    trigger_type: str,
    trigger_config: dict[str, Any],
    priority: str = "normal",
) -> None:
    if kind not in VALID_KINDS:
        msg = f"kind inválido: {kind!r}. Válidos: {sorted(VALID_KINDS)}"
        raise ValueError(msg)
    if trigger_type not in VALID_TRIGGERS:
        msg = f"trigger inválido: {trigger_type!r}. Válidos: {sorted(VALID_TRIGGERS)}"
        raise ValueError(msg)
    if priority not in VALID_PRIORITIES:
        msg = f"priority inválida: {priority!r}. Válidas: {sorted(VALID_PRIORITIES)}"
        raise ValueError(msg)
    if trigger_type == "interval":
        cron = (trigger_config or {}).get("cron_expr")
        if not cron:
            raise ValueError("trigger 'interval' requer trigger_config.cron_expr")
        # croniter levanta se o cron for inválido.
        croniter(cron, datetime.now(UTC))
    if trigger_type == "once" and (trigger_config or {}).get("cron_expr"):
        raise ValueError(
            "trigger 'once' não aceita trigger_config.cron_expr (não é recorrente)"
        )


async def _check_quota(conn: Any, workspace_id: str | None, trigger_type: str) -> None:
    """Levanta ValueError se o workspace já atingiu
    ``MAX_SCHEDULED_TASKS_PER_WORKSPACE`` tarefas não-manuais. Sem
    ``workspace_id`` (tarefas fora de um workspace) ou `trigger_type`
    'manual', não há quota — só recorrência/webhook autônomos contam."""
    if workspace_id is None or trigger_type == "manual":
        return
    cur = await conn.execute(
        "SELECT COUNT(*) AS cnt FROM vectora_background_tasks "
        "WHERE workspace_id = ? AND trigger_type != 'manual'",
        (workspace_id,),
    )
    row = await cur.fetchone()
    count = row["cnt"] if row else 0
    if count >= MAX_SCHEDULED_TASKS_PER_WORKSPACE:
        raise ValueError(
            f"limite de {MAX_SCHEDULED_TASKS_PER_WORKSPACE} tarefas agendadas "
            "por workspace atingido — pause ou remova tarefas existentes antes "
            "de criar novas"
        )


def _user_tzinfo() -> tzinfo:
    """Fuso configurado pelo usuário, ou o local do SO como fallback.

    Timezone inválido/indisponível degrada pro local com aviso (nunca
    lança) — um agendamento no fuso errado é melhor que o backend não
    subir, e o aviso deixa o problema visível no log.
    """
    try:
        from backend.workspace.runtime_settings import runtime_settings

        tz_name = runtime_settings.user_timezone
    except Exception:
        tz_name = ""
    if tz_name:
        try:
            return ZoneInfo(tz_name)
        except Exception:
            logger.warning(
                "background_tasks: timezone %r indisponível — usando local", tz_name
            )
    return datetime.now().astimezone().tzinfo or UTC


def _next_run(cron_expr: str | None) -> str | None:
    """Próximo disparo em UTC (formato de armazenamento) a partir do cron.

    O cron é interpretado no fuso do usuário — "0 9 * * 1" significa 9h da
    manhã pra quem escreveu, não 9h UTC. O retorno é sempre convertido pra
    UTC porque é o que `_list_due_interval_tasks` compara.
    """
    if not cron_expr:
        return None
    try:
        now_local = datetime.now(_user_tzinfo())
        nxt = croniter(cron_expr, now_local).get_next(datetime)
        return nxt.astimezone(UTC).isoformat()
    except Exception:
        logger.exception("background_tasks: cron inválido %r", cron_expr)
        return None


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


async def create_task(
    session_id: str,
    user_id: str,
    kind: str,
    name: str,
    instruction: str,
    trigger_type: str,
    trigger_config: dict[str, Any] | None = None,
    workspace_id: str | None = None,
    next_run_at: str | None = None,
    agent_profile_id: str | None = None,
    priority: str = "normal",
    board_id: str | None = None,
) -> BackgroundTask:
    """Cria uma tarefa. Levanta ValueError em kind/trigger/cron/priority inválidos.

    ``next_run_at`` — override explícito de quando disparar, usado por
    agendamentos de execução única (sem ``cron_expr`` recorrente, ex.
    ``schedule_subagent_task``). Sem isso, ``trigger_type="interval"``
    calcula normalmente a partir de ``trigger_config["cron_expr"]``.
    """
    cfg = trigger_config or {}
    _validate(kind, trigger_type, cfg, priority)
    if trigger_type == "webhook":
        require_pro()
    task_id = str(uuid4())
    if next_run_at is not None:
        next_run = next_run_at
    else:
        next_run = (
            _next_run(cfg.get("cron_expr")) if trigger_type == "interval" else None
        )

    # Task com data de disparo futura nasce em "scheduled" — distingue de
    # "todo" (sem data, esperando promoção por dependência) e de "ready"
    # (acionável agora, ex.: manual criada direto no board).
    status = "scheduled" if next_run is not None else "ready"

    conn = await _get_db()
    try:
        await _check_quota(conn, workspace_id, trigger_type)
        await conn.execute(
            """
            INSERT INTO vectora_background_tasks
              (id, session_id, workspace_id, user_id, kind, name, instruction,
               trigger_type, trigger_config, enabled, next_run_at, status,
               agent_profile_id, priority, board_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                session_id,
                workspace_id,
                user_id,
                kind,
                name,
                instruction,
                trigger_type,
                json.dumps(cfg),
                next_run,
                status,
                agent_profile_id,
                priority,
                board_id,
            ),
        )
        await conn.commit()
    finally:
        with contextlib.suppress(Exception):
            await conn.close()

    return BackgroundTask(
        id=task_id,
        session_id=session_id,
        user_id=user_id,
        kind=kind,
        name=name,
        instruction=instruction,
        trigger_type=trigger_type,
        trigger_config=cfg,
        workspace_id=workspace_id,
        enabled=True,
        next_run_at=next_run,
        status=status,
        agent_profile_id=agent_profile_id,
        priority=priority,
        board_id=board_id,
    )


async def list_tasks(session_id: str) -> list[BackgroundTask]:
    """Lista tarefas de uma session."""
    conn = await _get_db()
    try:
        cur = await conn.execute(
            "SELECT * FROM vectora_background_tasks WHERE session_id = ? "
            "ORDER BY created_at DESC",
            (session_id,),
        )
        rows = await cur.fetchall()
    finally:
        with contextlib.suppress(Exception):
            await conn.close()
    return [_row_to_task(r) for r in rows]


async def list_tasks_by_board(board_id: str) -> list[BackgroundTask]:
    """Lista tarefas de um board. Mesmo formato de
    `list_tasks`, escopado por `board_id` em vez de `session_id`."""
    conn = await _get_db()
    try:
        cur = await conn.execute(
            "SELECT * FROM vectora_background_tasks WHERE board_id = ? "
            "ORDER BY created_at DESC",
            (board_id,),
        )
        rows = await cur.fetchall()
    finally:
        with contextlib.suppress(Exception):
            await conn.close()
    return [_row_to_task(r) for r in rows]


async def get_task(task_id: str) -> BackgroundTask | None:
    """Retorna a tarefa pelo id, ou None."""
    conn = await _get_db()
    try:
        cur = await conn.execute(
            "SELECT * FROM vectora_background_tasks WHERE id = ?",
            (task_id,),
        )
        row = await cur.fetchone()
    finally:
        with contextlib.suppress(Exception):
            await conn.close()
    return _row_to_task(row) if row else None


async def update_task(task_id: str, **updates: Any) -> BackgroundTask | None:
    """Atualiza campos (name, instruction, enabled, trigger_config). Recalcula
    next_run_at quando o cron muda. Retorna a tarefa atualizada ou None."""
    task = await get_task(task_id)
    if task is None:
        return None

    sets: list[str] = []
    args: list[Any] = []
    if "name" in updates and updates["name"] is not None:
        sets.append("name = ?")
        args.append(updates["name"])
    if "instruction" in updates and updates["instruction"] is not None:
        sets.append("instruction = ?")
        args.append(updates["instruction"])
    if "enabled" in updates and updates["enabled"] is not None:
        sets.append("enabled = ?")
        args.append(1 if updates["enabled"] else 0)
    if "trigger_config" in updates and updates["trigger_config"] is not None:
        cfg = updates["trigger_config"]
        _validate(task.kind, task.trigger_type, cfg)
        sets.append("trigger_config = ?")
        args.append(json.dumps(cfg))
        if task.trigger_type == "interval":
            sets.append("next_run_at = ?")
            args.append(_next_run(cfg.get("cron_expr")))
    if "priority" in updates and updates["priority"] is not None:
        priority = updates["priority"]
        if priority not in VALID_PRIORITIES:
            msg = (
                f"priority inválida: {priority!r}. Válidas: {sorted(VALID_PRIORITIES)}"
            )
            raise ValueError(msg)
        sets.append("priority = ?")
        args.append(priority)
    if "agent_profile_id" in updates:
        # `None` explícito é um valor válido aqui (desatribuir) — por isso
        # não se filtra `is not None` como os outros campos: a chave
        # PRECISA estar presente em `updates` pra disparar o UPDATE, mas
        # uma vez presente, `None` é intencional (drawer "sem assignee").
        sets.append("agent_profile_id = ?")
        args.append(updates["agent_profile_id"])
    if not sets:
        return task

    sets.append("updated_at = ?")
    args.append(datetime.now(UTC).isoformat())
    args.append(task_id)

    conn = await _get_db()
    try:
        await conn.execute(
            f"UPDATE vectora_background_tasks SET {', '.join(sets)} WHERE id = ?",  # noqa: S608  # nosec B608
            tuple(args),
        )
        await conn.commit()
    finally:
        with contextlib.suppress(Exception):
            await conn.close()

    if "enabled" in updates and updates["enabled"] is not None:
        await _sync_status_with_enabled(task_id, enabled=bool(updates["enabled"]))

    return await get_task(task_id)


async def _sync_status_with_enabled(task_id: str, *, enabled: bool) -> None:
    """Mantém `status` (Kanban) coerente com o toggle `enabled`.

    Desabilitar tira a tarefa do fluxo ativo (`todo`). Reabilitar só devolve
    pra `ready` se não houver um `block_kind` ativo — reabilitar por cima de
    um bloqueio (ex.: budget estourado) fingiria que ele não existe mais.
    """
    try:
        from backend.scheduling import kanban

        if not enabled:
            await kanban.set_status(task_id, "todo")
            return

        estado = await kanban.get_task_status(task_id)
        if estado.get("block_kind"):
            return
        await kanban.set_status(task_id, "ready")
    except Exception:
        # Kanban é camada acessória — falha aqui não pode impedir o toggle,
        # que já foi persistido acima.
        logger.warning(
            "background_tasks: falha ao sincronizar status do kanban", exc_info=True
        )


async def delete_task(task_id: str) -> bool:
    """Remove a tarefa. Retorna False se não existia."""
    conn = await _get_db()
    try:
        cur = await conn.execute(
            "DELETE FROM vectora_background_tasks WHERE id = ?",
            (task_id,),
        )
        await conn.commit()
        return (cur.rowcount or 0) > 0
    finally:
        with contextlib.suppress(Exception):
            await conn.close()


# ---------------------------------------------------------------------------
# Runs (histórico)
# ---------------------------------------------------------------------------


async def _insert_run(
    run_id: str, task: BackgroundTask, run_thread_id: str, trigger_source: str
) -> None:
    conn = await _get_db()
    try:
        await conn.execute(
            """
            INSERT INTO vectora_background_runs
              (id, task_id, session_id, run_thread_id, trigger_source, status)
            VALUES (?, ?, ?, ?, ?, 'running')
            """,
            (run_id, task.id, task.session_id, run_thread_id, trigger_source),
        )
        await conn.commit()
    finally:
        with contextlib.suppress(Exception):
            await conn.close()


async def _finish_run(
    run_id: str,
    status: str,
    summary: str,
    *,
    expected_status: str | None = None,
) -> bool:
    """Finaliza uma run, opcionalmente sob fencing de estado.

    Runs retomadas passam ``expected_status="running"`` para que uma
    conclusão atrasada nunca sobrescreva uma transição terminal concorrente.
    """
    conn = await _get_db()
    try:
        if expected_status is not None:
            cur = await conn.execute(
                "UPDATE vectora_background_runs SET status = ?, summary = ?, "
                "finished_at = ? WHERE id = ? AND status = ?",
                (
                    status,
                    summary[:4000],
                    datetime.now(UTC).isoformat(),
                    run_id,
                    expected_status,
                ),
            )
        else:
            cur = await conn.execute(
                "UPDATE vectora_background_runs SET status = ?, summary = ?, "
                "finished_at = ? WHERE id = ?",
                (status, summary[:4000], datetime.now(UTC).isoformat(), run_id),
            )
        await conn.commit()
        return cur.rowcount > 0
    finally:
        with contextlib.suppress(Exception):
            await conn.close()


async def _transition_run_status(
    run_id: str,
    expected_status: str,
    status: str,
) -> bool:
    """Aplica uma transição de estado condicional e atômica à run."""
    conn = await _get_db()
    try:
        cur = await conn.execute(
            "UPDATE vectora_background_runs SET status = ? WHERE id = ? AND status = ?",
            (status, run_id, expected_status),
        )
        await conn.commit()
        return cur.rowcount > 0
    finally:
        with contextlib.suppress(Exception):
            await conn.close()


async def _await_reservation_step(operation: Awaitable[bool]) -> bool:
    """Conclui uma etapa de reserva mesmo se o chamador for cancelado."""
    operation_task = asyncio.ensure_future(operation)
    try:
        return await asyncio.shield(operation_task)
    except asyncio.CancelledError:
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.shield(operation_task)
        raise


async def _reopen_cancelled_run(run_id: str) -> bool:
    """Desfaz um cancelamento quando a transição do cartão não foi possível."""
    conn = await _get_db()
    try:
        cur = await conn.execute(
            "UPDATE vectora_background_runs SET status = 'awaiting_approval', "
            "finished_at = NULL WHERE id = ? AND status = 'cancelled'",
            (run_id,),
        )
        await conn.commit()
        return cur.rowcount > 0
    finally:
        with contextlib.suppress(Exception):
            await conn.close()


async def _reserve_resumed_run(run_id: str, task: BackgroundTask) -> bool:
    """Reserva run e card antes de entrar no motor, com compensação."""
    reserved = False
    try:
        if not await _await_reservation_step(
            _transition_run_status(run_id, "awaiting_approval", "running")
        ):
            return False
        reserved = True
        from backend.scheduling.kanban import ensure_task_claim

        if await _await_reservation_step(ensure_task_claim(task.id, run_id)):
            return True
        logger.warning(
            "background_tasks: run %s não recuperou o claim de %s",
            run_id,
            task.id,
        )
        await _await_reservation_step(
            _transition_run_status(run_id, "running", "awaiting_approval")
        )
        return False
    except asyncio.CancelledError:
        with contextlib.suppress(asyncio.CancelledError):
            await _await_reservation_step(
                _transition_run_status(run_id, "running", "awaiting_approval")
            )
        raise
    except Exception as exc:
        logger.exception(
            "background_tasks: reserva da run falhou",
            extra={"run_id": run_id, "task_id": task.id},
        )
        if reserved:
            with contextlib.suppress(Exception):
                await _finish_run(
                    run_id,
                    "error",
                    str(exc),
                    expected_status="running",
                )
            with contextlib.suppress(Exception):
                from backend.scheduling.kanban import block_task

                await block_task(
                    task.id,
                    "transient",
                    str(exc)[:500],
                    authorized_run_id=run_id,
                )
        return False


async def _mark_run_awaiting(run_id: str, summary: str) -> None:
    """Marca a run como pendente de aprovação humana (HITL).

    Diferente de ``_finish_run``: NÃO grava ``finished_at`` — a run não terminou,
    está esperando resume (approve/reject/edit).
    """
    conn = await _get_db()
    try:
        await conn.execute(
            "UPDATE vectora_background_runs SET status = 'awaiting_approval', "
            "summary = ? WHERE id = ?",
            (summary[:4000], run_id),
        )
        await conn.commit()
    finally:
        with contextlib.suppress(Exception):
            await conn.close()


async def _touch_last_run(task_id: str) -> None:
    conn = await _get_db()
    try:
        await conn.execute(
            "UPDATE vectora_background_tasks SET last_run_at = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(), task_id),
        )
        await conn.commit()
    finally:
        with contextlib.suppress(Exception):
            await conn.close()


async def list_runs(session_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Lista as execuções recentes de uma session."""
    conn = await _get_db()
    try:
        cur = await conn.execute(
            "SELECT * FROM vectora_background_runs WHERE session_id = ? "
            "ORDER BY started_at DESC LIMIT ?",
            (session_id, limit),
        )
        rows = await cur.fetchall()
    finally:
        with contextlib.suppress(Exception):
            await conn.close()
    return list(rows)


async def list_runs_for_user(
    session_id: str, user_id: str, limit: int = 50
) -> list[BackgroundRunRow]:
    """Lista apenas runs ligadas a tasks ainda pertencentes ao usuário.

    O histórico de uma task removida permanece armazenado para auditoria, mas
    não pode aparecer na API de uma sessão nativa que continua válida.
    """
    conn = await _get_db()
    try:
        cur = await conn.execute(
            """
            SELECT run.id, run.task_id, run.session_id, run.run_thread_id,
                   run.trigger_source, run.status, run.summary,
                   run.started_at, run.finished_at
            FROM vectora_background_runs AS run
            INNER JOIN vectora_background_tasks AS task
              ON task.id = run.task_id
             AND task.session_id = run.session_id
             AND task.user_id = ?
            WHERE run.session_id = ?
            ORDER BY run.started_at DESC
            LIMIT ?
            """,
            (user_id, session_id, limit),
        )
        rows = await cur.fetchall()
    finally:
        with contextlib.suppress(Exception):
            await conn.close()
    return [cast("BackgroundRunRow", row) for row in rows]


async def runs_have_owned_tasks(session_id: str, user_id: str) -> bool:
    """Informa se cada run retida ainda aponta para uma task do usuário.

    As linhas de run mantêm o histórico depois que uma task é apagada, mas
    não carregam um proprietário próprio. Uma linha órfã, portanto, não pode
    comprovar autorização para uma sessão sem registro nativo de propriedade.
    """
    conn = await _get_db()
    try:
        cur = await conn.execute(
            """
            SELECT 1
            FROM vectora_background_runs AS run
            LEFT JOIN vectora_background_tasks AS task
              ON task.id = run.task_id
             AND task.session_id = run.session_id
             AND task.user_id = ?
            WHERE run.session_id = ?
              AND task.id IS NULL
            LIMIT 1
            """,
            (user_id, session_id),
        )
        return await cur.fetchone() is None
    finally:
        with contextlib.suppress(Exception):
            await conn.close()


async def list_runs_for_task(task_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """Histórico de execuções de UMA task — o que falta pro card do Kanban
    conectar run history (hoje só `list_runs` por session existe, sem
    filtro por card)."""
    conn = await _get_db()
    try:
        cur = await conn.execute(
            "SELECT * FROM vectora_background_runs WHERE task_id = ? "
            "ORDER BY started_at DESC LIMIT ?",
            (task_id, limit),
        )
        rows = await cur.fetchall()
    finally:
        with contextlib.suppress(Exception):
            await conn.close()
    return list(rows)


# ---------------------------------------------------------------------------
# Delegação de subagente (tool `task`) — vira histórico na aba Tarefas
# ---------------------------------------------------------------------------


async def _get_or_create_subagent_anchor(
    session_id: str, user_id: str, subagent_type: str, workspace_id: str | None
) -> BackgroundTask:
    """Tarefa-âncora ``kind="subagent"`` por (session, subagent_type).

    Cada delegação via ``delegate_to_subagent`` roda dentro do mesmo turno,
    sem persistência própria — a âncora é criada uma vez e cada delegação
    vira um novo ``vectora_background_runs`` sob ela, igual a uma tarefa
    agendada acumulando histórico de execuções.
    """
    for t in await list_tasks(session_id):
        if (
            t.kind == "subagent"
            and t.trigger_config.get("subagent_type") == subagent_type
        ):
            return t
    return await create_task(
        session_id=session_id,
        user_id=user_id,
        kind="subagent",
        name=f"Subagente: {subagent_type}",
        instruction="",
        trigger_type="subagent",
        trigger_config={"subagent_type": subagent_type},
        workspace_id=workspace_id,
    )


async def record_subagent_delegation(
    session_id: str,
    user_id: str,
    subagent_type: str,
    description: str,
    status: str,
    summary: str,
    workspace_id: str | None = None,
) -> None:
    """Registra uma chamada à tool ``task()`` como execução na aba Tarefas.

    Best-effort — falha aqui nunca deve derrubar o stream de chat que a
    disparou (ver ``backend/api/native_stream.py::stream_engine_events``).
    """
    try:
        task = await _get_or_create_subagent_anchor(
            session_id, user_id, subagent_type, workspace_id
        )
        run_id = str(uuid4())
        await _insert_run(run_id, task, session_id, "subagent")
        await _finish_run(run_id, status, summary or description[:200])
        await _touch_last_run(task.id)
    except Exception:
        logger.exception(
            "record_subagent_delegation: falha ao registrar run subagent=%s",
            subagent_type,
        )


# ---------------------------------------------------------------------------
# Execução do agente
# ---------------------------------------------------------------------------


def _describe_pending_approval(pending: dict[str, Any] | None) -> str:
    """Descrição curta e humana do que a run está esperando aprovar — lê
    ``SessionStore.get_pending_approval``, que carrega uma única tool/args
    por pendência (não uma lista de ações candidatas)."""
    if not pending:
        return "Aguardando aprovação."
    return f"Aguardando aprovação: {pending.get('tool_name') or 'ação desconhecida'}"


def _soul_tool_registry(soul: Any, user_id: str | None) -> Any:
    """``ToolRegistry`` nativo com só as tools da SOUL pedida — mesmo
    filtro de RBAC (kill-switch global + ABAC por usuário) que
    ``agent_factory._native_subagent_catalog`` aplica no catálogo de
    delegação síncrona, replicado aqui pra execução agendada de uma SOUL
    isolada (``trigger_config.subagent_type``, ver ``schedule_subagent_task``).

    Mesma salvaguarda de ``agent_factory._native_tool_registry``/
    ``_native_subagent_catalog`` (achado real, revisão de 2026-08-30): sem
    importar ``backend.nodes.tools`` primeiro, ``soul.tools`` pode estourar
    ``ToolNameNotFoundError`` ao resolver um grupo (ex. ``fs`` →
    ``list_terminals``) que só é registrado por um módulo de tool nunca
    importado neste caminho de execução (agendamento roda fora do fluxo de
    ``get_native_agent``, que é quem normalmente garante isso)."""
    import backend.nodes.tools  # import registra todo @vtool no TOOL_REGISTRY
    from backend.rbac import tool_policy
    from backend.tools.registry import TOOL_REGISTRY, ToolRegistry

    disabled = tool_policy.effective_disabled(user_id)
    registry = ToolRegistry()
    for lc_tool in soul.tools:
        name = getattr(lc_tool, "name", "")
        if not name or name in disabled:
            continue
        spec = TOOL_REGISTRY.get(name)
        if spec is not None:
            registry.register(spec)
    return registry


def _emit_run_event(
    event: str, task: BackgroundTask, run_id: str, run_thread_id: str, summary: str = ""
) -> None:
    """Emite o estado da run no canal SSE de webhooks (consumido pelo painel)."""
    try:
        from backend.api.handlers.webhooks import _emit_sse_event

        _emit_sse_event(
            provider="background",
            event_type=f"background_run.{event}",
            data={
                "task_id": task.id,
                "task_name": task.name,
                "kind": task.kind,
                "session_id": task.session_id,
                "run_id": run_id,
                "run_thread_id": run_thread_id,
                "status": event,
                "summary": summary,
            },
        )
    except Exception:
        logger.debug("background_tasks: falha ao emitir SSE da run", exc_info=True)


async def _worktree_workspace_id(workspace_id: str, task_id: str) -> str:
    """Cria (ou reusa) um worktree isolado para a task e devolve o id do
    workspace efêmero apontando pra ele.

    Só tarefas em segundo plano passam por aqui. A delegação síncrona
    (``task()`` no meio de um turno do orchestrator) roda no workspace
    principal de propósito: ali o agente troca de persona pra um
    especialista dentro do mesmo turno, não é trabalho paralelo — isolar
    criaria um worktree por chamada e as edições ficariam invisíveis pro
    usuário, que está vendo o workspace principal. Paralelismo real, com
    duas runs mexendo em arquivo ao mesmo tempo, só acontece em segundo
    plano — é aqui que o isolamento é necessário.
    """
    from backend.scheduling.delegate import create_task_worktree
    from backend.workspace.workspace import workspace_registry

    worktree_path = await create_task_worktree(workspace_id, task_id)
    ws = workspace_registry.get_or_create(worktree_path)
    return ws.id


#: Bem abaixo do TTL do claim (900s, `kanban._DEFAULT_CLAIM_TTL_S`) — margem
#: generosa pra um heartbeat perdido não custar o claim inteiro.
_HEARTBEAT_INTERVAL_S = 60


async def _heartbeat_watchdog(task_id: str, run_id: str) -> None:
    """Estende o claim periodicamente enquanto a run está viva.

    Roda até ser cancelado (`run_task` cancela no `finally`, cobrindo
    sucesso/erro/interrupção/HITL pause). Falha ao estender não derruba a
    run — só fica sem o log de sucesso; `release_stale_claims()` no pior
    caso trata como run morta, o mesmo comportamento de antes deste
    watchdog existir."""
    from backend.scheduling.kanban import heartbeat_claim

    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
        try:
            await heartbeat_claim(task_id, run_id)
        except Exception:
            logger.warning(
                "background_tasks: heartbeat_claim falhou para %s",
                task_id,
                exc_info=True,
            )


async def run_task(
    task: BackgroundTask,
    trigger_source: str,
    payload: dict[str, Any] | None = None,
) -> str | None:
    """Executa o agente para a tarefa. Cria uma thread visível e grava a run.

    Defensiva: nunca propaga exceção — falha vira run com status 'error'. Retorna
    o `run_thread_id` em sucesso, ou None em erro.
    """
    # Corte por budget acontece **antes** de criar a run: barrar depois já
    # teria gasto. A run em andamento nunca é abortada no meio — ver
    # `backend/scheduling/budget.py`.
    try:
        from backend.scheduling.budget import check_budget

        if not await check_budget(task.id):
            logger.info(
                "background_tasks: run de %s barrada por budget estourado", task.id
            )
            return None
    except Exception:
        # Budget é camada acessória: falha aqui não pode impedir a tarefa de
        # rodar, que é a função principal.
        logger.warning("background_tasks: checagem de budget falhou", exc_info=True)

    run_id = str(uuid4())

    # Claim atômico via CAS: pega a task só se ainda estiver `ready` e sem
    # claim — evita duas execuções concorrentes da mesma task (tick do
    # scheduler cruzando com um disparo manual, por exemplo). Como o Kanban
    # é camada acessória, um erro aqui não pode impedir a run de rodar —
    # só a corrida real (claim_task devolvendo `False`) barra.
    try:
        from backend.scheduling.kanban import claim_task

        if not await claim_task(task.id, run_id):
            logger.info(
                "background_tasks: %s já está com claim tomado — pulando run",
                task.id,
            )
            return None
    except Exception:
        logger.warning("background_tasks: claim_task falhou", exc_info=True)

    run_thread_id = f"bg-{task.id}-{int(datetime.now(UTC).timestamp())}"
    await _insert_run(run_id, task, run_thread_id, trigger_source)
    _emit_run_event("started", task, run_id, run_thread_id)

    # Watchdog do claim: `run_task` executa a run INLINE numa única coroutine
    # (sem subprocess/PID/heartbeat externo, diferente do Hermes) — sem isso,
    # uma run genuína que passa do TTL do claim (900s) é devolvida pra `ready`
    # por `release_stale_claims()` no tick seguinte do scheduler, permitindo
    # reclaim/execução duplicada da MESMA task enquanto a primeira ainda roda.
    # Só prova que a coroutine está viva, não que está progredindo — uma run
    # travada num tool call infinito continua batendo heartbeat; estagnação
    # semântica é `classify_liveness`, que já roda no fim da run (linha acima
    # não mexida) e não tem relação com este watchdog.
    _watchdog_task = asyncio.create_task(_heartbeat_watchdog(task.id, run_id))

    try:
        from backend.services import agent_factory
        from backend.tools.subagent_delegate import SubagentDeps

        prompt = task.instruction
        model_override: str | None = None
        if task.agent_profile_id:
            # Perfil de agente customizado: a instrução da task é o "o
            # quê", a do perfil é o "como" — concatenadas, nunca uma
            # substituindo a outra. Falha ao carregar o perfil (apagado,
            # DB indisponível) degrada pro comportamento padrão da task,
            # nunca derruba a run.
            try:
                from backend.services.agent_profiles import get_profile

                profile = await get_profile(task.agent_profile_id)
                if profile is not None:
                    if profile.instruction_path:
                        try:
                            from pathlib import Path

                            persona = Path(profile.instruction_path).read_text(
                                encoding="utf-8"
                            )
                            prompt = f"{persona}\n\n---\n\n{prompt}"
                        except OSError:
                            logger.warning(
                                "background_tasks: instruction_path do perfil %s "
                                "ilegível",
                                task.agent_profile_id,
                                exc_info=True,
                            )
                    model_override = profile.model_override
            except Exception:
                logger.warning(
                    "background_tasks: falha ao carregar perfil %s da task %s",
                    task.agent_profile_id,
                    task.id,
                    exc_info=True,
                )

        if payload:
            evt = json.dumps(payload, ensure_ascii=False)[:4000]
            prompt = f"{prompt}\n\n## Evento recebido\n```json\n{evt}\n```"

        configurable: dict[str, Any] = {
            "thread_id": run_thread_id,
            "user_id": task.user_id,
            # Default "auto": sem humano no fluxo de fundo, o HITL dinâmico roda
            # sem pausas e a task conclui inteira. Uma task pode configurar um
            # modo que interrompe (trigger_config.permission_mode) — aí a run
            # fica "awaiting_approval" e é retomável via resume_background_run.
            "permission_mode": task.trigger_config.get("permission_mode", "auto"),
            # Identifica a task pro HITL de `kanban_update_status`: uma run
            # que se marca bloqueada (`task_id == background_task_id`) não
            # espera aprovação de si mesma — ver `backend/services/middleware.py`.
            "background_task_id": task.id,
            "background_run_id": run_id,
        }
        if task.workspace_id:
            configurable["workspace_id"] = task.workspace_id

        subagent_type = task.trigger_config.get("subagent_type")
        if subagent_type:
            from backend.agents.souls import SOUL_CATALOG

            # Agendamento de SOUL específica — usa um worktree isolado quando
            # a SOUL edita filesystem/git e a task tem workspace (evita
            # concorrência com o workspace principal do usuário).
            soul = SOUL_CATALOG.get(subagent_type)
            if soul is None:
                msg = f"subagent_type inválido: {subagent_type!r}"
                raise ValueError(msg)
            if soul.needs_worktree_isolation and task.workspace_id:
                configurable["workspace_id"] = await _worktree_workspace_id(
                    task.workspace_id, task.id
                )
            tool_registry = _soul_tool_registry(soul, task.user_id)
            system_prompt = soul.system_prompt
            subagent_catalog: dict[str, Any] = {}
        else:
            native_agent = await agent_factory.get_native_agent(
                user_id=task.user_id,
                chat_mode=False,
                workspace_id=task.workspace_id,
            )
            tool_registry = native_agent.tool_registry
            system_prompt = native_agent.system_prompt
            subagent_catalog = native_agent.subagent_catalog

        session_store = await agent_factory.get_session_store()
        approval_gate = await agent_factory.get_approval_gate()
        chat_client = FallbackChatClient(primary_model_id=model_override or "")
        loop_config = LoopConfig(max_iterations=50)

        run_ctx = ctx_from_config({"configurable": configurable})
        run_ctx.store = await agent_factory.get_store()
        if subagent_catalog:
            run_ctx._extra["subagent_deps"] = SubagentDeps(
                catalog=subagent_catalog,
                session_store=session_store,
                chat_client=chat_client,
                config=loop_config,
                should_require_approval=should_require_approval,
            )

        await session_store.create_session(
            run_thread_id,
            user_id=task.user_id,
            workspace_id=configurable.get("workspace_id"),
            parent_thread_id=task.session_id or None,
            mode="background",
            permission_mode=configurable.get("permission_mode", "auto"),
        )
        system_id = await session_store.append_message(
            run_thread_id, text_message(MessageRole.SYSTEM, system_prompt)
        )
        await session_store.append_message(
            run_thread_id,
            text_message(MessageRole.USER, prompt),
            parent_message_id=system_id,
        )

        goal_outcome = None
        if task.kind == "goal":
            goal_outcome = await run_goal(
                session_store=session_store,
                chat_client=chat_client,
                tool_registry=tool_registry,
                ctx=run_ctx,
                thread_id=run_thread_id,
                goal=task.instruction,
                loop_config=loop_config,
                quality_gates=task.trigger_config.get("quality_gates") or None,
                max_goal_turns=int(task.trigger_config.get("max_goal_turns", 20)),
                should_require_approval=should_require_approval,
                approval_gate=approval_gate,
            )
            stopped_reason = (
                "interrupted"
                if goal_outcome.status == "interrupted"
                else goal_outcome.status
            )
            final_message = goal_outcome.final_message
        else:
            result = await run_conversation(
                session_store=session_store,
                chat_client=chat_client,
                tool_registry=tool_registry,
                ctx=run_ctx,
                thread_id=run_thread_id,
                config=loop_config,
                should_require_approval=should_require_approval,
                approval_gate=approval_gate,
            )
            stopped_reason = result.stopped_reason
            final_message = result.final_message

        from backend.api.handlers.threads import (
            _increment_message_count,
            _upsert_session,
        )

        if task.kind == "goal":
            label = "Objetivo"
        elif task.kind == "routine":
            label = "Rotina"
        else:
            label = "Heartbreak"
        await _upsert_session(
            run_thread_id,
            title=f"{label}: {task.name}",
            workspace_id=task.workspace_id,
        )
        # message_count>0 faz a thread da run aparecer na sidebar (ListThreads
        # filtra message_count>0). O histórico completo sai de SessionStore via
        # GetHistory (a run roda sob run_thread_id + SessionStore compartilhado).
        await _increment_message_count(run_thread_id)

        # HITL: se o loop pausou numa ação destrutiva, a run fica pendente de
        # aprovação (não "done") — a pendência está persistida em
        # `pending_approvals` (SessionStore) e a run é retomável por
        # resume_background_run. Vale tanto pro loop normal quanto pro goal
        # loop (que nunca decide por cima de uma pendência HITL).
        if stopped_reason == "interrupted":
            pending = await session_store.get_pending_approval(run_thread_id)
            desc = _describe_pending_approval(pending)
            await _mark_run_awaiting(run_id, desc)
            await _touch_last_run(task.id)
            _emit_run_event("needs_approval", task, run_id, run_thread_id, desc)
            return run_thread_id

        # Goal loop esgotou o turn budget ou travou em falha repetida (mesmo
        # gate/judge) — terminal como uma exceção seria, mas sem propagar.
        if goal_outcome is not None and goal_outcome.status == "error":
            await _finish_run(run_id, "error", goal_outcome.reason)
            await _touch_last_run(task.id)
            _emit_run_event("error", task, run_id, run_thread_id, goal_outcome.reason)
            with contextlib.suppress(Exception):
                from backend.scheduling.kanban import block_task

                await block_task(
                    task.id,
                    "transient",
                    goal_outcome.reason[:500],
                    authorized_run_id=run_id,
                )
            return None

        summary = (
            final_message.text()
            if final_message
            else (goal_outcome.reason if goal_outcome is not None else "")
        )
        await _finish_run(run_id, "done", summary)

        # Custo real da run, gravado só agora que se sabe o resultado. O
        # motor nativo ainda não expõe `usage_metadata` pro caller do loop
        # (só o stream de chunks carrega `VMessageChunk.usage`, sem sink
        # agregador aqui) — custo entra como "desconhecido" (`None`), nunca
        # como zero, mesma distinção que `estimate_cost_cents` documenta.
        # Liveness é puramente informativa — nunca bloqueia nem pausa a task.
        try:
            from backend.scheduling.budget import estimate_cost_cents, record_run_cost
            from backend.scheduling.liveness import classify_liveness
            from backend.workspace.runtime_settings import runtime_settings

            resolved_model = model_override or (
                f"{runtime_settings.active_provider.replace('-', '_')}:"
                f"{runtime_settings.active_model}"
            )
            cost_cents = estimate_cost_cents(resolved_model, None)
            liveness = classify_liveness(summary)
            await record_run_cost(
                run_id,
                tokens_used=None,
                cost_cents=cost_cents,
                liveness=liveness,
            )
        except Exception:
            logger.warning(
                "background_tasks: falha ao registrar custo/liveness da run",
                exc_info=True,
            )

        await _touch_last_run(task.id)
        _emit_run_event("done", task, run_id, run_thread_id, summary)
        await report_to_parent_session(task, run_thread_id, summary)
        await _mark_kanban_after_success(task, run_id=run_id)
        return run_thread_id
    except Exception as exc:
        logger.exception(
            "background_tasks: run falhou",
            extra={"task_id": task.id, "trigger": trigger_source},
        )
        with contextlib.suppress(Exception):
            await _finish_run(run_id, "error", str(exc))
        _emit_run_event("error", task, run_id, run_thread_id, str(exc))
        with contextlib.suppress(Exception):
            from backend.scheduling.kanban import block_task

            # "transient" (não "capability"): é uma falha da própria run,
            # não do orçamento — a taxonomia distingue os dois motivos pro
            # card mostrar o certo.
            await block_task(
                task.id,
                "transient",
                str(exc)[:500],
                authorized_run_id=run_id,
            )
        return None
    finally:
        _watchdog_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await _watchdog_task


async def _mark_kanban_after_success(
    task: BackgroundTask, *, run_id: str | None = None
) -> None:
    """Fecha o ciclo do Kanban após uma run bem-sucedida.

    Recorrente (`interval`) nunca termina de verdade — volta pra `ready`
    pro próximo disparo, nunca `done` (que seria um estado terminal errado
    pra algo que roda de novo amanhã). Qualquer outro `trigger_type` (
    `manual`/`webhook`/`once`) é execução única: `done` é terminal — a
    menos que `trigger_config.requires_review` peça revisão humana antes,
    caso em que vai pra `review` em vez de `done` diretamente.
    `recompute_ready()` promove tasks que dependiam desta, quando existirem.
    Quando ``run_id`` é informado, a escrita é cercada pelo claim dessa run e
    libera o claim no mesmo ``UPDATE``; se a task se bloqueou durante a
    execução, o fencing falha e preserva o bloqueio.
    """
    try:
        from backend.scheduling.kanban import recompute_ready, set_status

        if task.trigger_type == "interval":
            novo_status = "ready"
        elif task.trigger_config.get("requires_review"):
            novo_status = "review"
        else:
            novo_status = "done"
        await set_status(task.id, novo_status, authorized_run_id=run_id)
        await recompute_ready()
    except Exception:
        logger.warning(
            "background_tasks: falha ao atualizar status do kanban pós-run",
            exc_info=True,
        )


async def _get_run(run_id: str) -> dict[str, Any] | None:
    """Uma run de background por id (ou ``None``)."""
    conn = await _get_db()
    try:
        cur = await conn.execute(
            "SELECT id, task_id, session_id, run_thread_id, status, summary "
            "FROM vectora_background_runs WHERE id = ?",
            (run_id,),
        )
        return await cur.fetchone()
    finally:
        with contextlib.suppress(Exception):
            await conn.close()


async def _resume_goal_run(
    *,
    run_id: str,
    task: BackgroundTask,
    run_thread_id: str,
    session_store: Any,
    chat_client: Any,
    tool_registry: Any,
    run_ctx: Any,
    loop_config: LoopConfig,
    resume_decision: str,
    edited_args: dict[str, Any] | None,
    approval_gate: Any,
) -> str | None:
    """Retoma uma run ``kind="goal"`` pausada em HITL — extraído de
    ``resume_background_run`` só pra manter o número de saídas da função
    principal dentro do teto de lint (mesmo comportamento, sem mudança de
    fluxo)."""
    goal_outcome = await resume_goal(
        session_store=session_store,
        chat_client=chat_client,
        tool_registry=tool_registry,
        ctx=run_ctx,
        thread_id=run_thread_id,
        goal=task.instruction,
        loop_config=loop_config,
        decision=resume_decision,
        edited_args=edited_args,
        quality_gates=task.trigger_config.get("quality_gates") or None,
        max_goal_turns=int(task.trigger_config.get("max_goal_turns", 20)),
        should_require_approval=should_require_approval,
        approval_gate=approval_gate,
    )
    if goal_outcome.status == "interrupted":
        pending = await session_store.get_pending_approval(run_thread_id)
        desc = _describe_pending_approval(pending)
        await _mark_run_awaiting(run_id, desc)
        _emit_run_event("needs_approval", task, run_id, run_thread_id, desc)
        return "awaiting_approval"
    if goal_outcome.status == "error":
        finished = await _finish_run(
            run_id,
            "error",
            goal_outcome.reason,
            expected_status="running",
        )
        if not finished:
            return None
        _emit_run_event("error", task, run_id, run_thread_id, goal_outcome.reason)
        with contextlib.suppress(Exception):
            from backend.scheduling.kanban import block_task

            await block_task(
                task.id,
                "transient",
                goal_outcome.reason[:500],
                authorized_run_id=run_id,
            )
        return None
    summary = (
        goal_outcome.final_message.text()
        if goal_outcome.final_message
        else goal_outcome.reason
    )
    finished = await _finish_run(run_id, "done", summary, expected_status="running")
    if not finished:
        return None
    _emit_run_event("done", task, run_id, run_thread_id, summary)
    await report_to_parent_session(task, run_thread_id, summary)
    await _mark_kanban_after_success(task, run_id=run_id)
    return "done"


async def _resume_normal_run(
    *,
    run_id: str,
    task: BackgroundTask,
    run_thread_id: str,
    session_store: Any,
    chat_client: Any,
    tool_registry: Any,
    run_ctx: Any,
    loop_config: LoopConfig,
    resume_decision: str,
    edited_args: dict[str, Any] | None,
    approval_gate: Any,
) -> str | None:
    """Retoma uma run de kind não-``"goal"`` — extraído de
    ``resume_background_run`` pelo mesmo motivo de ``_resume_goal_run``
    (teto de saídas do lint), sem mudança de comportamento."""
    resumed = await resume_conversation(
        session_store=session_store,
        tool_registry=tool_registry,
        ctx=run_ctx,
        thread_id=run_thread_id,
        decision=resume_decision,
        edited_args=edited_args,
        approval_gate=approval_gate,
    )
    if not resumed:
        # A reserva já foi feita; uma pendência ausente é uma falha terminal,
        # não um retorno silencioso que deixaria run e card presos em running.
        reason = "Aprovação pendente não encontrada para a run retomada."
        finished = await _finish_run(run_id, "error", reason, expected_status="running")
        if finished:
            with contextlib.suppress(Exception):
                from backend.scheduling.kanban import block_task

                await block_task(
                    task.id,
                    "transient",
                    reason,
                    authorized_run_id=run_id,
                )
        return None

    result = await run_conversation(
        session_store=session_store,
        chat_client=chat_client,
        tool_registry=tool_registry,
        ctx=run_ctx,
        thread_id=run_thread_id,
        config=loop_config,
        should_require_approval=should_require_approval,
        approval_gate=approval_gate,
    )

    if result.stopped_reason == "interrupted":
        pending = await session_store.get_pending_approval(run_thread_id)
        desc = _describe_pending_approval(pending)
        await _mark_run_awaiting(run_id, desc)
        _emit_run_event("needs_approval", task, run_id, run_thread_id, desc)
        return "awaiting_approval"

    summary = result.final_message.text() if result.final_message else ""
    finished = await _finish_run(run_id, "done", summary, expected_status="running")
    if not finished:
        return None
    _emit_run_event("done", task, run_id, run_thread_id, summary)
    await report_to_parent_session(task, run_thread_id, summary)
    await _mark_kanban_after_success(task, run_id=run_id)
    return "done"


async def resume_background_run(run_id: str, decision: str = "approve") -> str | None:
    """Retoma uma run de background pausada em HITL (``awaiting_approval``).

    A aprovação pendente está persistida em ``SessionStore.pending_approvals``
    sob o ``run_thread_id`` — resume via ``resume_conversation`` (motor
    nativo) e, se não houver mais pendência, continua o turno com
    ``run_conversation`` — mesmo mecanismo de duas etapas do ResumeChat do
    chat síncrono (``backend/api/handlers/chat.py``).

    Args:
        run_id: id da run em ``awaiting_approval``.
        decision: ``"approve"`` | ``"reject"`` | ``"edit:<json_dos_args>"``.

    Returns:
        Novo status (``"done"`` ou ``"awaiting_approval"`` se pausou de novo no
        mesmo turno), ou ``None`` se a run não existe/não está pendente ou falha.
    """
    run = await _get_run(run_id)
    if run is None or run.get("status") != "awaiting_approval":
        return None
    run_thread_id = run.get("run_thread_id") or ""
    task = await get_task(run["task_id"])
    if task is None or not run_thread_id:
        return None
    # Reserve the run before touching the task claim. The helper compensates
    # database failures so no reservation can strand a run in ``running``.
    if not await _reserve_resumed_run(run_id, task):
        return None

    watchdog_task: asyncio.Task[Any] | None = None
    try:
        watchdog_task = asyncio.create_task(_heartbeat_watchdog(task.id, run_id))

        from backend.services import agent_factory
        from backend.tools.subagent_delegate import SubagentDeps

        if decision == "approve":
            resume_decision = "approve"
            edited_args: dict[str, Any] | None = None
        elif decision == "reject":
            resume_decision = "reject"
            edited_args = None
        elif decision.startswith("edit:"):
            resume_decision = "edit"
            edited_args = json.loads(decision[5:])
        else:
            raise ValueError(f"decision inválida: {decision!r}")

        mode = task.trigger_config.get("permission_mode", "auto")
        configurable: dict[str, Any] = {
            "thread_id": run_thread_id,
            "user_id": task.user_id,
            "permission_mode": mode,
            "background_task_id": task.id,
            "background_run_id": run_id,
        }
        if task.workspace_id:
            configurable["workspace_id"] = task.workspace_id

        subagent_type = task.trigger_config.get("subagent_type")
        if subagent_type:
            from backend.agents.souls import SOUL_CATALOG

            soul = SOUL_CATALOG.get(subagent_type)
            if soul is None:
                msg = f"subagent_type inválido: {subagent_type!r}"
                raise ValueError(msg)
            tool_registry = _soul_tool_registry(soul, task.user_id)
            subagent_catalog: dict[str, Any] = {}
        else:
            native_agent = await agent_factory.get_native_agent(
                user_id=task.user_id,
                chat_mode=False,
                workspace_id=task.workspace_id,
            )
            tool_registry = native_agent.tool_registry
            subagent_catalog = native_agent.subagent_catalog

        session_store = await agent_factory.get_session_store()
        approval_gate = await agent_factory.get_approval_gate()
        chat_client = FallbackChatClient(primary_model_id="")
        loop_config = LoopConfig(max_iterations=50)

        run_ctx = ctx_from_config({"configurable": configurable})
        run_ctx.store = await agent_factory.get_store()
        if subagent_catalog:
            run_ctx._extra["subagent_deps"] = SubagentDeps(
                catalog=subagent_catalog,
                session_store=session_store,
                chat_client=chat_client,
                config=loop_config,
                should_require_approval=should_require_approval,
            )

        if task.kind == "goal":
            # Goal loop: `resume_goal` já resolve a pendência internamente
            # (via `resume_conversation`) e reentra no loop de objetivo pelos
            # turnos restantes — diferente do caminho normal abaixo, que só
            # retoma UM turno.
            return await _resume_goal_run(
                run_id=run_id,
                task=task,
                run_thread_id=run_thread_id,
                session_store=session_store,
                chat_client=chat_client,
                tool_registry=tool_registry,
                run_ctx=run_ctx,
                loop_config=loop_config,
                resume_decision=resume_decision,
                edited_args=edited_args,
                approval_gate=approval_gate,
            )

        return await _resume_normal_run(
            run_id=run_id,
            task=task,
            run_thread_id=run_thread_id,
            session_store=session_store,
            chat_client=chat_client,
            tool_registry=tool_registry,
            run_ctx=run_ctx,
            loop_config=loop_config,
            resume_decision=resume_decision,
            edited_args=edited_args,
            approval_gate=approval_gate,
        )
    except Exception as exc:
        logger.exception("background_tasks: resume falhou", extra={"run_id": run_id})
        with contextlib.suppress(Exception):
            await _finish_run(
                run_id,
                "error",
                str(exc),
                expected_status="running",
            )
        with contextlib.suppress(Exception):
            from backend.scheduling.kanban import block_task

            await block_task(
                task.id,
                "transient",
                str(exc)[:500],
                authorized_run_id=run_id,
            )
        return None
    finally:
        if watchdog_task is not None:
            watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watchdog_task


async def cancel_background_run(run_id: str | None) -> str | None:
    """Cancela uma run pendente de aprovação — status ``cancelled``.

    A run cancelada não é retomável. Retorna ``"cancelled"`` em sucesso, ou
    ``None`` se a run não existe ou já terminou (done/error/cancelled).

    O card também é bloqueado sob fencing quando a run ainda detém o claim.
    Assim, o cleanup de claims expirados não transforma um cancelamento
    explícito em uma nova execução automática.
    """
    if not run_id:
        return None
    run = await _get_run(run_id)
    if run is None or run.get("status") != "awaiting_approval":
        return None
    task_id = run.get("task_id")
    task = await get_task(task_id) if isinstance(task_id, str) else None
    summary = run.get("summary") or "Cancelada pelo usuário."
    # Reserve the terminal state first. If resume won the race, the run is
    # already ``running`` and cancellation must not interrupt its coroutine.
    if not await _finish_run(
        run_id,
        "cancelled",
        summary,
        expected_status="awaiting_approval",
    ):
        return None
    if task is not None:
        try:
            from backend.scheduling.kanban import block_task

            await block_task(
                task.id,
                "needs_input",
                "Execução cancelada pelo usuário.",
                authorized_run_id=run_id,
                allow_expired_claim=True,
            )
        except ValueError:
            logger.warning(
                "background_tasks: cancelamento perdeu o claim da task",
                extra={"run_id": run_id, "task_id": task.id},
            )
            await _reopen_cancelled_run(run_id)
            return None
    return "cancelled"


async def report_to_parent_session(
    task: BackgroundTask, run_thread_id: str, summary: str
) -> bool:
    """Posta o resultado da run COMO MENSAGEM na sessão-mãe (Hermes/Paperclip).

    Anexa uma mensagem ASSISTANT ao histórico da sessão que criou a task
    (``task.session_id``) via ``SessionStore.append_message`` — a próxima
    leitura de histórico (`SessionStore.get_history`, relida a cada iteração
    do loop nativo) já enxerga que a tarefa terminou (com resumo + link pra
    thread da run). ``create_session`` é ``INSERT OR IGNORE``: se a
    sessão-mãe ainda não existir em ``SessionStore`` (thread nunca tocada
    pelo motor nativo), é criada aqui na hora, sem precisar migrar histórico
    antigo.

    Best-effort: falha aqui nunca deve derrubar a conclusão da run. Retorna
    ``True`` se reportou, ``False`` se pulou (sem sessão-mãe) ou falhou.
    """
    if not task.session_id or task.session_id == run_thread_id:
        return False
    try:
        from backend.services import agent_factory

        session_store = await agent_factory.get_session_store()
        text = (
            f"🔔 Tarefa em segundo plano concluída — **{task.name}**\n\n"
            f"{summary or '(sem resumo)'}\n\n"
            f"_Histórico completo na thread `{run_thread_id}`._"
        )
        await session_store.create_session(task.session_id, user_id=task.user_id)
        parent_id = await session_store.get_branch_head_id(task.session_id)
        await session_store.append_message(
            task.session_id,
            text_message(MessageRole.ASSISTANT, text),
            parent_message_id=parent_id,
        )
        return True
    except Exception:
        logger.exception(
            "background_tasks: falha ao reportar à sessão-mãe",
            extra={"task_id": task.id, "session_id": task.session_id},
        )
        return False


# ---------------------------------------------------------------------------
# Webhook → IA
# ---------------------------------------------------------------------------


def _event_matches(event_type: str, events: list[str]) -> bool:
    """Casa o evento recebido contra os eventos configurados na task.

    Sem filtro (`events` vazio) casa tudo. Casa por igualdade exata ou pelo
    prefixo antes do ponto (ex.: configurado 'pull_request' casa
    'pull_request.opened').
    """
    if not events:
        return True
    base = event_type.split(".", 1)[0]
    return any(e in (event_type, base) for e in events)


async def dispatch_webhook_event(
    provider: str, event_type: str, payload: dict[str, Any]
) -> int:
    """Dispara as tasks 'webhook' habilitadas cujo filtro casa. Retorna quantas.

    Ponte webhook→IA: chamada por `receive_webhook` após persistir o evento.
    """
    conn = await _get_db()
    try:
        cur = await conn.execute(
            "SELECT * FROM vectora_background_tasks "
            "WHERE enabled = 1 AND trigger_type = 'webhook'",
        )
        rows = await cur.fetchall()
    except Exception:
        logger.exception("background_tasks: falha ao listar webhook tasks")
        return 0
    finally:
        with contextlib.suppress(Exception):
            await conn.close()

    fired = 0
    for row in rows:
        task = _row_to_task(row)
        cfg = task.trigger_config
        want_provider = cfg.get("provider")
        if want_provider and want_provider != provider:
            continue
        if not _event_matches(event_type, list(cfg.get("events") or [])):
            continue
        await run_task(task, "webhook", payload=payload)
        fired += 1
    return fired


# ---------------------------------------------------------------------------
# GitHub Issues → Kanban (caminho determinístico, sem LLM)
# ---------------------------------------------------------------------------

#: Corpo da issue truncado ao virar instrução do card — evita instrução
#: gigante numa issue com descrição extensa.
_ISSUE_BODY_MAX_CHARS = 2000

#: Ações do evento `issues` que o sync entende. Qualquer outra (labeled,
#: milestoned, etc.) é ignorada — não vira efeito no board.
_ISSUE_SYNC_ACTIONS = frozenset({"opened", "closed", "reopened", "edited", "assigned"})


async def _find_github_issue_sync_anchor() -> BackgroundTask | None:
    """Task 'webhook' que liga eventos `issues` do GitHub ao Kanban.

    O usuário liga o sync criando (UI/API) uma task com trigger_type='webhook'
    e trigger_config={"provider": "github", "events": [... "issues" ...]} — os
    cards de issue nascem na mesma session/workspace dessa task-âncora. Sem
    task assim (habilitada), o sync está desligado: nenhum card é criado ou
    atualizado a partir de eventos `issues`.
    """
    conn = await _get_db()
    try:
        cur = await conn.execute(
            "SELECT * FROM vectora_background_tasks "
            "WHERE enabled = 1 AND trigger_type = 'webhook'",
        )
        rows = await cur.fetchall()
    finally:
        with contextlib.suppress(Exception):
            await conn.close()
    for row in rows:
        task = _row_to_task(row)
        cfg = task.trigger_config
        if cfg.get("provider") != "github":
            continue
        if _event_matches("issues", list(cfg.get("events") or [])):
            return task
    return None


async def find_task_by_github_issue(
    session_id: str, repo: str, issue_number: int
) -> BackgroundTask | None:
    """Card já ligado a essa issue (mesmo `repo`+`issue_number`), se existir.

    Chave de idempotência do sync — reentrega do mesmo webhook (o GitHub
    reenvia quando não recebe 200 a tempo) atualiza o card em vez de duplicar.
    """
    for t in await list_tasks(session_id):
        cfg = t.trigger_config
        if (
            cfg.get("source") == "github_issue"
            and cfg.get("repo") == repo
            and cfg.get("issue_number") == issue_number
        ):
            return t
    return None


async def _upsert_github_issue_card(
    anchor: BackgroundTask,
    existing: BackgroundTask | None,
    title: str,
    body: str,
    cfg: dict[str, Any],
) -> BackgroundTask | None:
    """Cria o card na primeira vez que a issue chega; atualiza numa reentrega."""
    if existing is not None:
        await update_task(existing.id, name=title, instruction=body, trigger_config=cfg)
        return await get_task(existing.id)
    return await create_task(
        session_id=anchor.session_id,
        user_id=anchor.user_id,
        kind="routine",
        name=title,
        instruction=body,
        trigger_type="manual",
        trigger_config=cfg,
        workspace_id=anchor.workspace_id,
    )


async def sync_github_issue_to_kanban(
    action: str, repo: str, issue: dict[str, Any]
) -> BackgroundTask | None:
    """Espelha um evento `issues` do GitHub num card do Kanban — sem LLM no meio.

    `opened` cria (ou atualiza, se a issue já tem card — reentrega de
    webhook) um card `trigger_type='manual'`: fica no board, acionável sob
    demanda, mas nunca dispara sozinho como uma task `webhook`/`interval`
    faria — a ponte webhook→IA (`dispatch_webhook_event`) não vê essas
    tasks. `closed`/`reopened` só movem o card via `kanban.set_status`.
    `edited`/`assigned` atualizam título/instrução sem mudar status.

    Retorna `None` sem efeito quando: não há task-âncora configurada (sync
    desligado), a `action` não é reconhecida, ou o payload não traz
    `issue.number`. Defensiva por fora (`_handle_github` já roda dentro do
    try/except do dispatcher de webhook), mas nunca levanta por si só.
    """
    if action not in _ISSUE_SYNC_ACTIONS:
        return None
    issue_number = issue.get("number")
    if issue_number is None or not repo:
        return None

    anchor = await _find_github_issue_sync_anchor()
    if anchor is None:
        return None

    existing = await find_task_by_github_issue(anchor.session_id, repo, issue_number)
    title = str(issue.get("title") or f"Issue #{issue_number}")
    body = str(issue.get("body") or "")[:_ISSUE_BODY_MAX_CHARS]
    cfg = {
        "source": "github_issue",
        "repo": repo,
        "issue_number": issue_number,
        "html_url": issue.get("html_url") or "",
    }

    if action == "opened":
        return await _upsert_github_issue_card(anchor, existing, title, body, cfg)

    if existing is None:
        return None

    if action in ("closed", "reopened"):
        from backend.scheduling import kanban

        await kanban.set_status(existing.id, "done" if action == "closed" else "ready")
    else:  # edited, assigned
        await update_task(existing.id, name=title, instruction=body)
    return await get_task(existing.id)


# ---------------------------------------------------------------------------
# Alertas de observabilidade → Kanban (caminho determinístico, sem LLM)
#
# Contrato genérico pra qualquer ferramenta de alerta (Sentry, Grafana,
# PagerDuty, etc.) — sem parsing nativo de vendor nenhum. O provider aponta
# seu webhook de saída pra POST /webhook/observability com o payload já no
# formato {title, description, severity, url, external_id} (ver docs/content/
# guides/observability-webhooks.en.md).
# ---------------------------------------------------------------------------

#: Chave de agrupamento das tasks 'webhook' que ligam alertas de
#: observabilidade ao Kanban — mesmo papel do `provider == "github"` em
#: `_find_github_issue_sync_anchor`.
_OBSERVABILITY_PROVIDER = "observability"

#: Marca os cards criados por este sync no `trigger_config.source`, junto da
#: chave de idempotência (`external_id`) — mesmo papel do `"github_issue"`.
_OBSERVABILITY_SOURCE = "observability_alert"

#: `severity` → status inicial do card. `critical`/`high` precisam de
#: atenção imediata (`triage`); `medium`/`low` entram na fila normal
#: (`todo`). Severidade ausente ou desconhecida cai em `todo` — nunca
#: escala sozinha pra `triage` sem o sinal explícito do alertador.
_SEVERITY_TO_STATUS: dict[str, str] = {
    "critical": "triage",
    "high": "triage",
    "medium": "todo",
    "low": "todo",
}


async def _find_observability_sync_anchor() -> BackgroundTask | None:
    """Task 'webhook' que liga alertas de observabilidade ao Kanban.

    Mesmo princípio do `_find_github_issue_sync_anchor`: o usuário liga o
    sync criando uma task com trigger_type='webhook' e
    trigger_config={"provider": "observability"} — os cards de alerta nascem
    na mesma session/workspace dessa task-âncora. Sem task assim
    (habilitada), o sync está desligado.
    """
    conn = await _get_db()
    try:
        cur = await conn.execute(
            "SELECT * FROM vectora_background_tasks "
            "WHERE enabled = 1 AND trigger_type = 'webhook'",
        )
        rows = await cur.fetchall()
    finally:
        with contextlib.suppress(Exception):
            await conn.close()
    for row in rows:
        task = _row_to_task(row)
        if task.trigger_config.get("provider") == _OBSERVABILITY_PROVIDER:
            return task
    return None


async def find_task_by_observability_alert(
    session_id: str, external_id: str
) -> BackgroundTask | None:
    """Card já ligado a esse `external_id`, se existir.

    Chave de idempotência do sync — reentrega do mesmo alerta (retry do
    alertador, at-least-once delivery) atualiza o card em vez de duplicar.
    """
    for t in await list_tasks(session_id):
        cfg = t.trigger_config
        if (
            cfg.get("source") == _OBSERVABILITY_SOURCE
            and cfg.get("external_id") == external_id
        ):
            return t
    return None


async def sync_observability_alert_to_kanban(
    alert: dict[str, Any],
) -> BackgroundTask | None:
    """Espelha um alerta de observabilidade genérico num card do Kanban.

    Determinístico, sem LLM no meio — mesmo caminho de
    `sync_github_issue_to_kanban`. `severity` decide o status inicial via
    `_SEVERITY_TO_STATUS`. Reentrega do mesmo `alert["external_id"]`
    atualiza nome/descrição/status do card existente em vez de duplicar.

    Retorna `None` sem efeito quando não há task-âncora configurada (sync
    desligado). Espera `alert` já validado (`title`/`external_id`
    presentes) — quem chama (`receive_observability_webhook`) valida antes.
    """
    anchor = await _find_observability_sync_anchor()
    if anchor is None:
        return None

    from backend.scheduling import kanban

    external_id = str(alert["external_id"])
    title = str(alert["title"])
    description = str(alert.get("description") or "")[:_ISSUE_BODY_MAX_CHARS]
    severity = str(alert.get("severity") or "").lower()
    status = _SEVERITY_TO_STATUS.get(severity, "todo")
    cfg = {
        "source": _OBSERVABILITY_SOURCE,
        "external_id": external_id,
        "severity": severity,
        "url": str(alert.get("url") or ""),
    }

    existing = await find_task_by_observability_alert(anchor.session_id, external_id)
    if existing is not None:
        await update_task(
            existing.id, name=title, instruction=description, trigger_config=cfg
        )
        await kanban.set_status(existing.id, status)
        return await get_task(existing.id)

    task = await create_task(
        session_id=anchor.session_id,
        user_id=anchor.user_id,
        kind="routine",
        name=title,
        instruction=description,
        trigger_type="manual",
        trigger_config=cfg,
        workspace_id=anchor.workspace_id,
    )
    await kanban.set_status(task.id, status)
    return await get_task(task.id)


# ---------------------------------------------------------------------------
# Scheduler (interval)
# ---------------------------------------------------------------------------


async def _list_due_interval_tasks() -> list[BackgroundTask]:
    now = datetime.now(UTC).isoformat()
    conn = await _get_db()
    try:
        cur = await conn.execute(
            "SELECT * FROM vectora_background_tasks "
            "WHERE enabled = 1 AND trigger_type IN ('interval', 'once') "
            "AND next_run_at IS NOT NULL AND next_run_at <= ?",
            (now,),
        )
        rows = await cur.fetchall()
    finally:
        with contextlib.suppress(Exception):
            await conn.close()
    return [_row_to_task(r) for r in rows]


def _is_stale(next_run_at: str | None, now: datetime) -> bool:
    """True se o disparo agendado ficou pra trás além da janela de tolerância.

    Timestamp ausente/ilegível nunca é considerado atrasado — na dúvida
    executa, é melhor um disparo a mais que engolir a tarefa em silêncio.
    """
    if not next_run_at:
        return False
    try:
        scheduled = datetime.fromisoformat(next_run_at)
    except ValueError:
        return False
    if scheduled.tzinfo is None:
        scheduled = scheduled.replace(tzinfo=UTC)
    return scheduled < now - CATCH_UP_GRACE


async def _set_next_run(task_id: str, next_run: str | None) -> None:
    conn = await _get_db()
    try:
        await conn.execute(
            "UPDATE vectora_background_tasks SET next_run_at = ? WHERE id = ?",
            (next_run, task_id),
        )
        await conn.commit()
    finally:
        with contextlib.suppress(Exception):
            await conn.close()


class BackgroundScheduler:
    """Loop asyncio que dispara tasks 'interval' vencidas (tick de 60s)."""

    def __init__(self) -> None:
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("BackgroundScheduler iniciado")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        logger.info("BackgroundScheduler parado")

    async def _loop(self) -> None:
        try:
            while self._running:
                with contextlib.suppress(Exception):
                    await self.tick()
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            pass

    async def tick(self) -> None:
        """Executa as interval/once tasks vencidas.

        "interval" reagenda pelo cron; "once" (execução única) só desabilita
        a task depois de rodar — nunca refira sozinha.

        Recorrente atrasada além de ``CATCH_UP_GRACE`` (processo ficou horas
        parado) pula pro próximo horário em vez de disparar retroativamente:
        um "resumo diário das 9h" não deve rodar às 15h só porque a máquina
        estava desligada. "once" nunca é pulada — é uma tarefa que o usuário
        pediu uma vez, atrasar não a torna indesejada.
        """
        # Claims expirados voltam pra `ready` antes de qualquer disparo:
        # worker que morreu sem liberar deixaria o card preso em `running`.
        try:
            from backend.scheduling.kanban import (
                recompute_ready,
                release_stale_claims,
            )

            await release_stale_claims()
            await recompute_ready()
        except Exception:
            # Kanban é camada acessória — falha aqui não pode impedir o
            # disparo das tarefas agendadas, que é a função principal do tick.
            logger.warning("background_tasks: tick do kanban falhou", exc_info=True)

        now = datetime.now(UTC)
        for task in await _list_due_interval_tasks():
            if task.trigger_type == "interval" and _is_stale(task.next_run_at, now):
                logger.info(
                    "background_tasks: pulando disparo atrasado de %s "
                    "(agendado pra %s) — reagendando pro próximo horário",
                    task.id,
                    task.next_run_at,
                )
                await _set_next_run(
                    task.id, _next_run(task.trigger_config.get("cron_expr"))
                )
                continue
            await run_task(task, task.trigger_type)
            if task.trigger_type == "once":
                await update_task(task.id, enabled=False)


_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    """Singleton do BackgroundScheduler."""
    global _scheduler
    if _scheduler is None:
        _scheduler = BackgroundScheduler()
    return _scheduler
