"""Tool para criar tarefas em segundo plano via agente.

Permite ao agente registrar rotinas (cron), heartbreaks (webhook) ou
tarefas manuais em nome da sessão ativa.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
from typing import Any

from backend.scheduling import background_tasks
from backend.scheduling.nl_schedule import parse_natural_schedule, parse_one_shot_delay
from backend.scheduling.subagent_runner import SUBAGENT_TYPES
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)


def _capability_token(task_id: str) -> str:
    """HMAC(secret, task_id) — capability token de uma tarefa delegada via
    `schedule_subagent_task`. Recomputável a partir só do `task_id` (não
    precisa ser persistido): a mesma chave de assinatura de sessão já usada
    por `backend/rbac/auth.py` (auto-gerada por instalação, sempre
    disponível mesmo sem VECTORA_TOKEN/Pro configurado — nunca introduz
    segredo novo)."""
    from backend.rbac.auth import _get_secret

    return hmac.new(
        _get_secret().encode(), task_id.encode(), hashlib.sha256
    ).hexdigest()


def _requires_capability_token(task: Any) -> bool:
    """Só tasks criadas por `schedule_subagent_task` (têm `subagent_type`
    no trigger_config) exigem token — tasks de `create_background_task`
    continuam acessíveis como sempre, sem quebrar o fluxo existente."""
    return bool((task.trigger_config or {}).get("subagent_type"))


def _valid_capability_token(task_id: str, token: str | None) -> bool:
    if not token:
        return False
    return hmac.compare_digest(_capability_token(task_id), token)


async def _find_task_by_correlation_id(
    session_id: str, correlation_id: str
) -> Any | None:
    """Task já existente na mesma sessão com o mesmo `correlation_id` —
    usado por `schedule_subagent_task` pra deduplicar retry/race sem criar
    uma segunda delegação da mesma intenção."""
    tasks = await background_tasks.list_tasks(session_id)
    for t in tasks:
        if (t.trigger_config or {}).get("correlation_id") == correlation_id:
            return t
    return None


@vtool(
    extras=ToolExtras(
        invalidates=["tasks"],
        destructive=False,
        category="workspace",
        icon="clock",
    )
)
async def create_background_task(
    name: str,
    instruction: str,
    kind: str,
    trigger_type: str,
    ctx: ToolContext,
    trigger_config: dict[str, Any] | None = None,
) -> str:
    """Cria uma tarefa em segundo plano para esta sessão.

    Tarefas rodam o agente autonomamente conforme o trigger configurado.

    Args:
        name: Nome curto da tarefa (ex: "Verificar logs diariamente")
        instruction: Instrução completa que o agente executará
        kind: Tipo — routine (periódica) | heartbreak (via webhook)
        trigger_type: Gatilho — interval (cron) | webhook | manual
        trigger_config: Config do trigger (ex: {"cron_expr": "0 9 * * *"})
    """
    try:
        session_id = ctx.thread_id
        user_id = ctx.user_id

        if not session_id:
            return json.dumps(
                {"status": "error", "error": "session_id ausente no config"}
            )

        task = await background_tasks.create_task(
            session_id=session_id,
            user_id=user_id,
            kind=kind,
            name=name,
            instruction=instruction,
            trigger_type=trigger_type,
            trigger_config=trigger_config or {},
        )
        return json.dumps({"status": "created", "task_id": task.id, "name": task.name})

    except ValueError as e:
        return json.dumps({"status": "error", "error": str(e)})
    except Exception as e:
        logger.exception("create_background_task: erro inesperado")
        return json.dumps({"status": "error", "error": str(e)})


@vtool(
    extras=ToolExtras(
        invalidates=["tasks"],
        destructive=False,
        category="workspace",
        icon="calendar-clock",
    )
)
async def schedule_task(
    name: str,
    instruction: str,
    when: str,
    ctx: ToolContext,
) -> str:
    """Agenda uma tarefa recorrente a partir de uma descrição em linguagem
    natural do horário (ex.: "todo dia às 9h", "toda sexta-feira às 18h",
    "a cada 2 horas") — não exige que você (o agente) escreva a expressão
    cron manualmente.

    Só reconhece padrões recorrentes comuns em pt-BR. Se `when` não casar
    com nenhum padrão (ambíguo, vago, ou pedido de execução única tipo
    "daqui 2 horas" — não expressável como recorrência), retorna erro
    pedindo pra reformular em vez de adivinhar um horário errado.

    Args:
        name: Nome curto da tarefa (ex: "Resumo diário de commits")
        instruction: Instrução completa que o agente executará quando disparar
        when: Horário em linguagem natural (ex: "todo dia às 9h")

    Returns:
        JSON com `status`, e em caso de sucesso `task_id` + `next_run_at`
        (a próxima execução calculada — confirme com o usuário antes de
        considerar o agendamento definitivo).
    """
    cron_expr = parse_natural_schedule(when)
    if cron_expr is None:
        return json.dumps(
            {
                "status": "error",
                "error": (
                    f"Não entendi o horário '{when}'. Tente algo como "
                    "'todo dia às 9h', 'toda segunda às 14h' ou 'a cada 2 horas'."
                ),
            }
        )

    try:
        session_id = ctx.thread_id
        user_id = ctx.user_id
        if not session_id:
            return json.dumps(
                {"status": "error", "error": "session_id ausente no config"}
            )

        task = await background_tasks.create_task(
            session_id=session_id,
            user_id=user_id,
            kind="routine",
            name=name,
            instruction=instruction,
            trigger_type="interval",
            trigger_config={"cron_expr": cron_expr},
        )
        return json.dumps(
            {
                "status": "created",
                "task_id": task.id,
                "name": task.name,
                "cron_expr": cron_expr,
                "next_run_at": task.next_run_at,
            }
        )
    except ValueError as e:
        return json.dumps({"status": "error", "error": str(e)})
    except Exception as e:
        logger.exception("schedule_task: erro inesperado")
        return json.dumps({"status": "error", "error": str(e)})


@vtool(
    extras=ToolExtras(
        invalidates=["tasks"],
        destructive=False,
        category="workspace",
        icon="bot",
    )
)
async def schedule_subagent_task(
    subagent_type: str,
    description: str,
    when: str,
    ctx: ToolContext,
    correlation_id: str | None = None,
) -> str:
    """Agenda uma SOUL ESPECÍFICA do catálogo — não o agente principal
    completo — pra rodar sozinha numa hora futura, a partir de uma descrição
    em linguagem natural do horário (ex.: "em 30 minutos", "daqui 2 horas").
    Distinto de `schedule_task`: aquela reagenda o orchestrator completo
    recorrentemente; esta dispara só a SOUL pedida, uma única vez.

    Quando a SOUL edita filesystem/git e a sessão tem um workspace ativo, a
    execução roda num worktree isolado (mesma proteção contra concorrência
    da delegação síncrona via `task()`) — nunca disputa arquivos com o
    workspace principal do usuário.

    Devolve um `capability_token` junto do `task_id` — exigido por
    `get_task_status`/`get_task_result` pra consultar essa task
    especificamente (evita que um `task_id` vazado, ex. em log, seja
    consultável por outra sessão só por adivinhação).

    Args:
        subagent_type: nome de uma SOUL do catálogo (ver `SUBAGENT_TYPES`).
        description: O que a SOUL deve fazer (vira a instrução).
        when: Horário em linguagem natural (ex.: "em 30 minutos", "daqui 1 hora").
        correlation_id: identificador opcional da intenção de delegação —
            uma segunda chamada com o mesmo valor (mesma sessão) devolve a
            task já criada em vez de duplicar (protege contra retry/race).

    Returns:
        JSON com `status`, e em caso de sucesso `task_id` + `run_at` +
        `capability_token`.
    """
    if subagent_type not in SUBAGENT_TYPES:
        return json.dumps(
            {
                "status": "error",
                "error": f"subagent_type inválido: {subagent_type!r}. "
                f"Válidos: {SUBAGENT_TYPES}",
            }
        )

    run_at = parse_one_shot_delay(when)
    if run_at is None:
        return json.dumps(
            {
                "status": "error",
                "error": (
                    f"Não entendi o horário '{when}'. Tente algo como "
                    "'em 30 minutos' ou 'daqui 2 horas'."
                ),
            }
        )

    try:
        session_id = ctx.thread_id
        user_id = ctx.user_id
        workspace_id = ctx.workspace_id or None
        if not session_id:
            return json.dumps(
                {"status": "error", "error": "session_id ausente no config"}
            )

        if correlation_id:
            existing = await _find_task_by_correlation_id(session_id, correlation_id)
            if existing is not None:
                return json.dumps(
                    {
                        "status": "created",
                        "task_id": existing.id,
                        "subagent_type": subagent_type,
                        "run_at": existing.next_run_at,
                        "capability_token": _capability_token(existing.id),
                        "deduped": True,
                    }
                )

        trigger_config: dict[str, Any] = {"subagent_type": subagent_type}
        if correlation_id:
            trigger_config["correlation_id"] = correlation_id

        task = await background_tasks.create_task(
            session_id=session_id,
            user_id=user_id,
            kind="routine",
            name=f"Subagente {subagent_type}: {description[:60]}",
            instruction=description,
            trigger_type="once",
            trigger_config=trigger_config,
            workspace_id=workspace_id,
            next_run_at=run_at,
        )
        return json.dumps(
            {
                "status": "created",
                "task_id": task.id,
                "subagent_type": subagent_type,
                "run_at": task.next_run_at,
                "capability_token": _capability_token(task.id),
            }
        )
    except ValueError as e:
        return json.dumps({"status": "error", "error": str(e)})
    except Exception as e:
        logger.exception("schedule_subagent_task: erro inesperado")
        return json.dumps({"status": "error", "error": str(e)})


@vtool(extras=ToolExtras(destructive=False, category="workspace", icon="list"))
async def list_background_tasks(ctx: ToolContext) -> str:
    """Lista as tarefas em segundo plano desta sessão e o status da última run.

    Use para saber o que está rodando/agendado antes de responder ao usuário
    (ex.: "tenho 2 rotinas ativas, uma terminou com X"). Não recebe argumentos —
    a sessão vem do contexto.

    Returns:
        JSON com ``tasks``: id, name, kind, enabled, last_run_at e o status/resumo
        da execução mais recente (``last_run``), quando houver.
    """
    try:
        session_id = ctx.thread_id
        if not session_id:
            return json.dumps({"status": "error", "error": "session_id ausente"})

        tasks = await background_tasks.list_tasks(session_id)
        runs = await background_tasks.list_runs(session_id)
        latest_by_task: dict[str, dict[str, Any]] = {}
        for r in runs:  # list_runs vem ordenado por started_at DESC
            latest_by_task.setdefault(r["task_id"], r)

        out = []
        for t in tasks:
            last = latest_by_task.get(t.id)
            out.append(
                {
                    "task_id": t.id,
                    "name": t.name,
                    "kind": t.kind,
                    "enabled": t.enabled,
                    "last_run_at": t.last_run_at,
                    "last_run": (
                        {
                            "run_id": last["id"],
                            "status": last["status"],
                            "summary": (last.get("summary") or "")[:300],
                        }
                        if last
                        else None
                    ),
                }
            )
        return json.dumps({"status": "ok", "tasks": out}, ensure_ascii=False)
    except Exception as e:
        logger.exception("list_background_tasks: erro inesperado")
        return json.dumps({"status": "error", "error": str(e)})


@vtool(extras=ToolExtras(destructive=False, category="workspace", icon="info"))
async def get_task_status(
    task_id: str,
    ctx: ToolContext,
    capability_token: str | None = None,
) -> str:
    """Status de uma tarefa específica + suas execuções recentes.

    Args:
        task_id: id da tarefa (de ``list_background_tasks`` ou ``create_background_task``).
        capability_token: obrigatório só para tasks criadas por
            ``schedule_subagent_task`` (devolvido na criação) — tasks de
            ``create_background_task`` não exigem token.

    Returns:
        JSON com os campos da task e a lista de runs recentes (status/summary).
    """
    try:
        task = await background_tasks.get_task(task_id)
        if task is None:
            return json.dumps({"status": "error", "error": "task não encontrada"})
        if _requires_capability_token(task) and not _valid_capability_token(
            task_id, capability_token
        ):
            return json.dumps(
                {"status": "error", "error": "capability_token ausente ou inválido"}
            )
        runs = await background_tasks.list_runs(task.session_id)
        task_runs = [
            {
                "run_id": r["id"],
                "status": r["status"],
                "summary": (r.get("summary") or "")[:300],
                "started_at": r.get("started_at"),
                "finished_at": r.get("finished_at"),
            }
            for r in runs
            if r["task_id"] == task_id
        ]
        return json.dumps(
            {
                "status": "ok",
                "task_id": task.id,
                "name": task.name,
                "kind": task.kind,
                "enabled": task.enabled,
                "last_run_at": task.last_run_at,
                "runs": task_runs,
            },
            ensure_ascii=False,
        )
    except Exception as e:
        logger.exception("get_task_status: erro inesperado")
        return json.dumps({"status": "error", "error": str(e)})


@vtool(
    extras=ToolExtras(
        invalidates=["tasks"],
        destructive=True,
        category="workspace",
        icon="check",
    )
)
async def approve_task_action(
    run_id: str,
    ctx: ToolContext,
    decision: str = "approve",
) -> str:
    """Intervém numa run de background pausada aguardando aprovação (HITL).

    Quando uma task roda num modo que interrompe em ações destrutivas, a run
    fica ``awaiting_approval``. Use isto para retomá-la (o orquestrador pode
    aprovar/rejeitar em nome do usuário).

    Args:
        run_id: id da run em ``awaiting_approval`` (de ``list_background_tasks``).
        decision: ``approve`` | ``reject`` | ``edit:<json_dos_args>`` | ``cancel``.
            ``cancel`` encerra a run como cancelada (não retomável).

    Returns:
        JSON com o novo status ("done" | "awaiting_approval" | "cancelled") ou erro.
    """
    try:
        if decision == "cancel":
            res = await background_tasks.cancel_background_run(run_id)
        else:
            res = await background_tasks.resume_background_run(run_id, decision)
        if res is None:
            return json.dumps(
                {"status": "error", "error": "run não está pendente ou não existe"}
            )
        return json.dumps({"status": "ok", "run_status": res})
    except Exception as e:
        logger.exception("approve_task_action: erro inesperado")
        return json.dumps({"status": "error", "error": str(e)})


@vtool(
    extras=ToolExtras(
        invalidates=["tasks"],
        destructive=False,
        category="workspace",
        icon="power",
    )
)
async def toggle_background_task(task_id: str, enabled: bool) -> str:
    """Liga ou desliga uma tarefa em segundo plano já existente, sem apagá-la.

    Args:
        task_id: id da tarefa (de ``list_background_tasks``).
        enabled: ``True`` reativa, ``False`` pausa (não dispara mais sozinha).

    Returns:
        JSON com o novo estado ou erro se a tarefa não existe.
    """
    try:
        task = await background_tasks.update_task(task_id, enabled=enabled)
        if task is None:
            return json.dumps({"status": "error", "error": "task não encontrada"})
        return json.dumps({"status": "ok", "task_id": task.id, "enabled": task.enabled})
    except Exception as e:
        logger.exception("toggle_background_task: erro inesperado")
        return json.dumps({"status": "error", "error": str(e)})


@vtool(
    extras=ToolExtras(
        invalidates=["tasks"],
        destructive=True,
        category="workspace",
        icon="trash-2",
    )
)
async def delete_background_task(task_id: str) -> str:
    """Remove uma tarefa em segundo plano permanentemente.

    Args:
        task_id: id da tarefa a remover.

    Returns:
        JSON com ``status="ok"`` (idempotente — remover de novo não é erro).
    """
    try:
        await background_tasks.delete_task(task_id)
        return json.dumps({"status": "ok", "task_id": task_id})
    except Exception as e:
        logger.exception("delete_background_task: erro inesperado")
        return json.dumps({"status": "error", "error": str(e)})


@vtool(
    extras=ToolExtras(
        invalidates=["tasks"],
        destructive=False,
        category="workspace",
        icon="play",
    )
)
async def run_background_task_now(task_id: str, ctx: ToolContext) -> str:
    """Dispara a execução imediata de uma tarefa agendada, sem esperar o
    próximo horário do cron.

    Args:
        task_id: id da tarefa a executar agora.

    Returns:
        JSON com ``status="queued"`` (a execução roda em background — consulte
        ``get_task_status``/``list_background_tasks`` para acompanhar), ou
        erro se a tarefa não existe.
    """
    try:
        task = await background_tasks.get_task(task_id)
        if task is None:
            return json.dumps({"status": "error", "error": "task não encontrada"})
        if not ctx.thread_id:
            return json.dumps({"status": "error", "error": "session_id ausente"})
        if task.session_id != ctx.thread_id or task.user_id != ctx.user_id:
            return json.dumps(
                {"status": "error", "error": "task não pertence à sessão atual"}
            )
        asyncio.create_task(  # noqa: RUF006 — fire-and-forget, mesmo padrão do endpoint REST
            background_tasks.run_task(task, "manual")
        )
        return json.dumps({"status": "queued", "task_id": task_id})
    except Exception as e:
        logger.exception("run_background_task_now: erro inesperado")
        return json.dumps({"status": "error", "error": str(e)})


@vtool(extras=ToolExtras(destructive=False, category="workspace", icon="file-text"))
async def get_task_result(
    run_id: str,
    ctx: ToolContext,
    capability_token: str | None = None,
) -> str:
    """Resultado (resumo) de uma execução específica de tarefa.

    Args:
        run_id: id da run (de ``list_background_tasks``/``get_task_status``).
        capability_token: obrigatório só se a task dona da run foi criada
            por ``schedule_subagent_task`` (devolvido na criação).

    Returns:
        JSON com status, summary e a thread da run (para abrir o histórico
        completo), ou erro se a run não existe.
    """
    try:
        run = await background_tasks._get_run(run_id)
        if run is None:
            return json.dumps({"status": "error", "error": "run não encontrada"})
        owner_task = await background_tasks.get_task(run["task_id"])
        if owner_task is not None and _requires_capability_token(owner_task):
            if not _valid_capability_token(owner_task.id, capability_token):
                return json.dumps(
                    {
                        "status": "error",
                        "error": "capability_token ausente ou inválido",
                    }
                )
        return json.dumps(
            {
                "status": "ok",
                "run_id": run["id"],
                "run_status": run["status"],
                "summary": run.get("summary") or "",
                "run_thread_id": run.get("run_thread_id"),
            },
            ensure_ascii=False,
        )
    except Exception as e:
        logger.exception("get_task_result: erro inesperado")
        return json.dumps({"status": "error", "error": str(e)})
