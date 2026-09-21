"""Handler REST de tarefas em segundo plano — scoped por session do chat.

Rotina (cron), Heartbreak (evento/webhook) e disparo manual vivem dentro de uma
session (`thread_id`), pois é a session que guarda config + workspace + histórico.

Endpoints (todos exigem autenticação):
    GET    /sessions/{thread_id}/background/tasks            — lista tasks da session
    POST   /sessions/{thread_id}/background/tasks            — cria task
    PATCH  /sessions/{thread_id}/background/tasks/{task_id}  — atualiza/habilita
    DELETE /sessions/{thread_id}/background/tasks/{task_id}  — remove
    POST   /sessions/{thread_id}/background/tasks/{task_id}/run — dispara manual
    GET    /sessions/{thread_id}/background/runs             — histórico de execuções
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from pydantic import BaseModel

if TYPE_CHECKING:
    from backend.scheduling.background_tasks import BackgroundTask

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/sessions/{thread_id}/background", tags=["background"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TaskDependencyOut(BaseModel):
    id: str
    name: str
    status: str


class ProgressOut(BaseModel):
    done: int
    total: int


class TaskOut(BaseModel):
    id: str
    session_id: str
    workspace_id: str | None
    kind: str
    name: str
    instruction: str
    trigger_type: str
    trigger_config: dict[str, Any]
    enabled: bool
    last_run_at: str | None
    next_run_at: str | None
    status: str
    block_kind: str | None = None
    block_reason: str | None = None
    #: `workspace_id` já é o "tenant" do card; `agent_profile_id` é o
    #: "assignee".
    agent_profile_id: str | None = None
    priority: str = "normal"
    #: Pais diretos em `vectora_task_links`, com status atual — fonte real
    #: do contador N/M do editor de dependência no card (antes o frontend
    #: declarava `blocked_by` mas nada preenchia).
    dependencies: list[TaskDependencyOut] = []
    #: `None` sem claim ativo (task não `running`, ou já finalizada). Prova
    #: que o watchdog está vivo — junto do heartbeat real, destrava o arc
    #: animado.
    claim_expires_at: str | None = None
    #: Rollup de subtasks diretas (`kanban_decompose`). `None` — não
    #: `{0,0}` — quando a task não tem subtask nenhuma (a maioria); o card
    #: só desenha barra de progresso quando há algo pra medir.
    progress: ProgressOut | None = None
    comment_count: int = 0
    #: `None` = task sem board associado (multi-board opcional).
    board_id: str | None = None


class BoardColumnOut(BaseModel):
    status: str
    tasks: list[TaskOut]


class BoardOut(BaseModel):
    """`GET .../board`. Substitui N chamadas client-side (uma
    lista plana + o front reagrupando) por uma passada só: colunas já na
    ordem canônica, com os agregados (progress/comment_count/dependencies)
    computados em lote (`get_progress_batch` e afins), não um por card."""

    columns: list[BoardColumnOut]
    #: `workspace_id`/`agent_profile_id` distintos entre as tasks da
    #: session — popula os dropdowns de filtro sem o frontend precisar
    #: derivar isso da lista de tasks como faz hoje.
    tenants: list[str]
    assignees: list[str]


class CreateTaskRequest(BaseModel):
    kind: str
    name: str
    instruction: str
    trigger_type: str
    trigger_config: dict[str, Any] = {}
    workspace_id: str | None = None
    priority: str = "normal"
    #: `create_task` (background_tasks.py) já suporta o campo — só faltava
    #: expor no schema HTTP pro formulário de nova tarefa poder setá-lo.
    agent_profile_id: str | None = None
    #: Board pra associar a task na criação. Opcional —
    #: uma task sem board continua funcionando normalmente, só não
    #: aparece em nenhuma visão de `/boards/{id}/board`.
    board_id: str | None = None


class UpdateTaskRequest(BaseModel):
    name: str | None = None
    instruction: str | None = None
    enabled: bool | None = None
    trigger_config: dict[str, Any] | None = None
    #: Transição manual de status (drag-and-drop no board) — validada contra
    #: `kanban.MANUAL_TRANSITIONS`, nunca um `UPDATE` direto.
    status: str | None = None
    priority: str | None = None
    #: `None` é um valor válido aqui (desatribuir) — por isso o handler
    #: só repassa este campo quando o cliente de fato o incluiu no corpo
    #: (`model_fields_set`), nunca filtrando por `is not None` como os
    #: demais campos.
    agent_profile_id: str | None = None


class BulkTaskActionRequest(BaseModel):
    task_ids: list[str]
    action: str


class BulkTaskResult(BaseModel):
    task_id: str
    ok: bool
    error: str | None = None


class RunOut(BaseModel):
    id: str
    task_id: str
    run_thread_id: str | None
    trigger_source: str
    status: str
    summary: str | None
    started_at: str
    finished_at: str | None


class CommentOut(BaseModel):
    id: str
    task_id: str
    user_id: str
    body: str
    created_at: str


class CreateCommentRequest(BaseModel):
    body: str


class TaskEventOut(BaseModel):
    id: str
    task_id: str
    from_status: str | None
    to_status: str
    block_kind: str | None
    block_reason: str | None
    created_at: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(status_code=401, detail="Não autenticado")
    return str(user.id)


def _build_task_out(
    t: BackgroundTask,
    *,
    dependencies: list[dict[str, Any]],
    progress: dict[str, int] | None,
    comment_count: int,
) -> TaskOut:
    """Monta o `TaskOut` a partir de `BackgroundTask` + os 3 agregados
    (dependências/progresso/comentários) — compartilhado entre `_to_out`
    (busca 1 a 1) e `get_board` (busca em lote), pra não duplicar
    a lista de campos em dois lugares que precisariam ficar sincronizados
    a cada campo novo."""
    return TaskOut(
        id=t.id,
        session_id=t.session_id,
        workspace_id=t.workspace_id,
        kind=t.kind,
        name=t.name,
        instruction=t.instruction,
        trigger_type=t.trigger_type,
        trigger_config=t.trigger_config,
        enabled=t.enabled,
        last_run_at=t.last_run_at,
        next_run_at=t.next_run_at,
        status=t.status,
        block_kind=t.block_kind,
        block_reason=t.block_reason,
        agent_profile_id=t.agent_profile_id,
        priority=t.priority,
        dependencies=[TaskDependencyOut(**d) for d in dependencies],
        claim_expires_at=t.claim_expires_at,
        progress=ProgressOut(**progress) if progress else None,
        comment_count=comment_count,
        board_id=t.board_id,
    )


async def _to_out(t: BackgroundTask) -> TaskOut:
    from backend.scheduling.kanban import get_dependencies, get_progress, list_comments

    deps = await get_dependencies(t.id)
    progress = await get_progress(t.id)
    comment_count = len(await list_comments(t.id))
    return _build_task_out(
        t, dependencies=deps, progress=progress, comment_count=comment_count
    )


async def _require_task(
    thread_id: str, task_id: str, *, request: Request | None = None
) -> BackgroundTask:
    """Carrega a task garantindo que pertence à session da URL."""
    from backend.scheduling.background_tasks import get_task

    task = await get_task(task_id)
    if (
        task is None
        or task.session_id != thread_id
        or (request is not None and task.user_id != _user_id(request))
    ):
        raise HTTPException(status_code=404, detail="Task não encontrada")
    return task


async def _require_thread_access(
    thread_id: str, request: Request, *, require_existing: bool = True
) -> str:
    """Require that the authenticated caller owns the session in the URL."""
    uid = _user_id(request)
    # The native session store is the source of truth for thread ownership.
    # Keep the task-owner check below as a defense in depth for legacy sessions
    # that have not yet been reconciled into that store.
    from backend.api.handlers.threads import (
        _assert_owns_thread,
        _get_session_store,
        _is_thread_deleted,
        _reconcile_delete_lock,
    )
    from backend.scheduling.background_tasks import (
        list_tasks,
        runs_have_owned_tasks,
    )

    session_store = await _get_session_store()
    session = await session_store.get_session(thread_id)
    if session is None and require_existing:
        # Tasks written by older releases can predate the native session row.
        # Keep those records readable by their recorded owner, while refusing
        # tombstoned IDs and mixed-owner task sets. New task creation always
        # reserves the native session first, so this is only a compatibility
        # path for legacy data and isolated task stores.
        async with _reconcile_delete_lock:
            if await _is_thread_deleted(thread_id):
                raise HTTPException(status_code=404, detail="Thread não encontrada")
            tasks_without_native_session = await list_tasks(thread_id)
            if (
                tasks_without_native_session
                and all(task.user_id == uid for task in tasks_without_native_session)
                and await runs_have_owned_tasks(thread_id, uid)
            ):
                return uid

    await _assert_owns_thread(thread_id, request, require_existing=require_existing)

    tasks = await list_tasks(thread_id)
    if any(task.user_id != uid for task in tasks):
        raise HTTPException(status_code=404, detail="Thread não encontrada")
    if not await runs_have_owned_tasks(thread_id, uid):
        raise HTTPException(status_code=404, detail="Thread não encontrada")
    if require_existing and not tasks:
        # Empty sessions are valid before their first background task. There is
        # no data to disclose or mutate, so retain the existing empty response.
        return uid
    return uid


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/tasks", response_model=list[TaskOut])
async def get_tasks(request: Request, thread_id: str) -> list[TaskOut]:
    from backend.scheduling.background_tasks import list_tasks

    await _require_thread_access(thread_id, request)
    return [await _to_out(t) for t in await list_tasks(thread_id)]


@router.get("/board", response_model=BoardOut)
async def get_board(request: Request, thread_id: str) -> BoardOut:
    """Board agregado numa passada só. Query O(1) por agregado
    (progress/comentários/dependências em lote), não O(n) por card — trava
    a regressão de N+1 que a listagem plana (`get_tasks` + `_to_out` por
    item) sempre teve."""
    from backend.scheduling.background_tasks import list_tasks
    from backend.scheduling.kanban import (
        KANBAN_STATUSES,
        get_comment_counts_batch,
        get_dependencies_batch,
        get_progress_batch,
    )

    await _require_thread_access(thread_id, request)
    tasks = await list_tasks(thread_id)
    ids = [t.id for t in tasks]

    progress_map = await get_progress_batch(ids)
    comments_map = await get_comment_counts_batch(ids)
    deps_map = await get_dependencies_batch(ids)

    by_status: dict[str, list[TaskOut]] = {}
    tenants: set[str] = set()
    assignees: set[str] = set()
    for t in tasks:
        if t.workspace_id:
            tenants.add(t.workspace_id)
        if t.agent_profile_id:
            assignees.add(t.agent_profile_id)
        out = _build_task_out(
            t,
            dependencies=deps_map.get(t.id, []),
            progress=progress_map.get(t.id),
            comment_count=comments_map.get(t.id, 0),
        )
        by_status.setdefault(t.status, []).append(out)

    # Ordem canônica primeiro; qualquer status fora de KANBAN_STATUSES (não
    # deveria existir — set_status valida — mas defensivo contra dado
    # legado/corrompido) entra depois, sem sumir da resposta.
    ordem = list(KANBAN_STATUSES) + [s for s in by_status if s not in KANBAN_STATUSES]
    columns = [BoardColumnOut(status=s, tasks=by_status.get(s, [])) for s in ordem]
    return BoardOut(
        columns=columns,
        tenants=sorted(tenants),
        assignees=sorted(assignees),
    )


@router.post("/tasks", response_model=TaskOut, status_code=201)
async def post_task(
    request: Request, thread_id: str, body: CreateTaskRequest
) -> TaskOut:
    from backend.api.handlers.threads import (
        _get_session_store,
        _is_thread_deleted,
        _reconcile_delete_lock,
    )
    from backend.scheduling.background_tasks import create_task

    uid = _user_id(request)
    if body.workspace_id:
        from backend.api.handlers.workspaces import require_workspace_access

        # Validate the workspace before reserving a generated session ID. A
        # rejected task must not leave behind a native session owned by the
        # caller, and the validated workspace becomes authoritative metadata
        # for the first task-created session.
        require_workspace_access(body.workspace_id, request)
    # A first Kanban task may arrive before the first chat turn persists the
    # generated thread. Reserve that ID only while holding the same lock used
    # by DeleteThread, and reject durable tombstones before registration. This
    # keeps the legitimate first-task path while making deleted IDs one-way.
    async with _reconcile_delete_lock:
        if await _is_thread_deleted(thread_id):
            raise HTTPException(status_code=404, detail="Thread não encontrada")
        session_store = await _get_session_store()
        if await session_store.get_session(thread_id) is None:
            await session_store.create_session(
                thread_id, user_id=uid, workspace_id=body.workspace_id
            )
    uid = await _require_thread_access(thread_id, request)
    if body.board_id:
        from backend.scheduling.boards import get_board

        board = await get_board(body.board_id)
        if board is None or board.user_id != uid:
            raise HTTPException(status_code=404, detail="Board não encontrado")
    try:
        task = await create_task(
            session_id=thread_id,
            user_id=uid,
            kind=body.kind,
            name=body.name,
            instruction=body.instruction,
            trigger_type=body.trigger_type,
            trigger_config=body.trigger_config,
            workspace_id=body.workspace_id,
            priority=body.priority,
            agent_profile_id=body.agent_profile_id,
            board_id=body.board_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("background: falha ao criar task")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return await _to_out(task)


#: Ações suportadas por `PATCH /tasks/bulk`. Só "archive" nesta leva — outras
#: ações em lote (ex.: cancelar) entram quando o produto pedir.
_BULK_ACTIONS = {"archive"}


@router.patch("/tasks/bulk", response_model=list[BulkTaskResult])
async def bulk_tasks_endpoint(
    request: Request, thread_id: str, body: BulkTaskActionRequest
) -> list[BulkTaskResult]:
    """Ação em lote da barra de seleção múltipla do Kanban.

    Cada `task_id` roda isolado (try/except por item): uma falha (id
    inexistente, task de outra session) não aborta os demais — a resposta
    reporta sucesso/erro por-item em vez de tudo-ou-nada.
    """
    from backend.scheduling import kanban

    await _require_thread_access(thread_id, request)
    if body.action not in _BULK_ACTIONS:
        raise HTTPException(
            status_code=400, detail=f"ação {body.action!r} não suportada"
        )

    results: list[BulkTaskResult] = []
    for task_id in body.task_ids:
        try:
            await _require_task(thread_id, task_id, request=request)
            await kanban.set_status(task_id, "archived")
            results.append(BulkTaskResult(task_id=task_id, ok=True))
        except HTTPException as exc:
            results.append(
                BulkTaskResult(task_id=task_id, ok=False, error=str(exc.detail))
            )
        except Exception as exc:
            logger.exception(
                "background: falha ao aplicar ação em lote",
                extra={"task_id": task_id, "action": body.action},
            )
            results.append(BulkTaskResult(task_id=task_id, ok=False, error=str(exc)))
    return results


@router.patch("/tasks/{task_id}", response_model=TaskOut)
async def patch_task(
    request: Request, thread_id: str, task_id: str, body: UpdateTaskRequest
) -> TaskOut:
    from backend.scheduling import kanban
    from backend.scheduling.background_tasks import update_task

    await _require_thread_access(thread_id, request)
    await _require_task(thread_id, task_id, request=request)
    if body.status is not None:
        try:
            await kanban.manual_transition(task_id, body.status)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    update_kwargs: dict[str, Any] = {
        "name": body.name,
        "instruction": body.instruction,
        "enabled": body.enabled,
        "trigger_config": body.trigger_config,
        "priority": body.priority,
    }
    if "agent_profile_id" in body.model_fields_set:
        update_kwargs["agent_profile_id"] = body.agent_profile_id
    try:
        updated = await update_task(task_id, **update_kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="Task não encontrada")
    return await _to_out(updated)


@router.delete("/tasks/{task_id}", status_code=204)
async def delete_task_endpoint(request: Request, thread_id: str, task_id: str) -> None:
    from backend.scheduling.background_tasks import delete_task

    await _require_thread_access(thread_id, request)
    await _require_task(thread_id, task_id, request=request)
    await delete_task(task_id)


class CreateLinkRequest(BaseModel):
    parent_id: str


@router.post("/tasks/{task_id}/links", response_model=TaskOut, status_code=201)
async def add_link_endpoint(
    request: Request, thread_id: str, task_id: str, body: CreateLinkRequest
) -> TaskOut:
    """`add_dependency` (`kanban.py`) só era chamada internamente pela tool
    `kanban_decompose` do agente — sem rota HTTP, o drawer não tinha como
    editar dependências. Os dois lados do vínculo precisam pertencer à
    mesma session (`_require_task` valida posse), senão um usuário
    linkaria a task de outra thread."""
    from backend.scheduling.background_tasks import get_task
    from backend.scheduling.kanban import add_dependency

    await _require_thread_access(thread_id, request)
    await _require_task(thread_id, task_id, request=request)
    await _require_task(thread_id, body.parent_id, request=request)
    try:
        await add_dependency(body.parent_id, task_id)
    except ValueError as exc:
        # Ciclo detectado — 409 (conflito com o estado atual do grafo de
        # dependências), não 400 (não é o payload que está mal formado).
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    updated = await get_task(task_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Task não encontrada")
    return await _to_out(updated)


@router.delete("/tasks/{task_id}/links/{parent_id}", response_model=TaskOut)
async def remove_link_endpoint(
    request: Request, thread_id: str, task_id: str, parent_id: str
) -> TaskOut:
    from backend.scheduling.background_tasks import get_task
    from backend.scheduling.kanban import remove_dependency

    await _require_thread_access(thread_id, request)
    await _require_task(thread_id, task_id, request=request)
    await remove_dependency(parent_id, task_id)
    updated = await get_task(task_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Task não encontrada")
    return await _to_out(updated)


@router.post("/tasks/{task_id}/unblock", response_model=TaskOut)
async def unblock_task_endpoint(
    request: Request, thread_id: str, task_id: str
) -> TaskOut:
    """Botão "Desbloquear" do Kanban — limpa `block_kind`/`block_reason` e
    devolve a tarefa pra `ready`, competindo pelo claim de novo."""
    from backend.scheduling.background_tasks import get_task
    from backend.scheduling.kanban import unblock_task

    await _require_thread_access(thread_id, request)
    await _require_task(thread_id, task_id, request=request)
    await unblock_task(task_id)
    updated = await get_task(task_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Task não encontrada")
    return await _to_out(updated)


@router.post("/tasks/{task_id}/review/approve", response_model=TaskOut)
async def approve_review_endpoint(
    request: Request, thread_id: str, task_id: str
) -> TaskOut:
    """Aprova uma task em `review`, movendo pra `done` — endpoint dedicado
    (não a transição genérica `PATCH .../tasks/{id}`) pra registrar quem
    aprovou e nunca abrir `review→done` pro drag-and-drop genérico."""
    from backend.scheduling.background_tasks import get_task
    from backend.scheduling.kanban import approve_review

    uid = await _require_thread_access(thread_id, request)
    await _require_task(thread_id, task_id, request=request)
    try:
        await approve_review(task_id, uid)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    updated = await get_task(task_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Task não encontrada")
    return await _to_out(updated)


@router.post("/tasks/{task_id}/run", status_code=202)
async def run_task_endpoint(
    request: Request,
    thread_id: str,
    task_id: str,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    from backend.scheduling.background_tasks import run_task

    await _require_thread_access(thread_id, request)
    task = await _require_task(thread_id, task_id, request=request)
    background_tasks.add_task(run_task, task, "manual")
    return {"status": "queued", "task_id": task_id}


class ResumeRunRequest(BaseModel):
    decision: str = "approve"  # "approve" | "reject" | "edit:<json_dos_args>"


@router.post("/runs/{run_id}/resume", status_code=202)
async def resume_run_endpoint(
    request: Request,
    thread_id: str,
    run_id: str,
    body: ResumeRunRequest,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """Retoma (approve/reject/edit) OU cancela (``decision="cancel"``) uma run
    pausada em HITL (``awaiting_approval``).

    O resume roda em background (invoca o agente); o novo status chega via o
    evento SSE ``background_run.done``/``needs_approval`` e no ``GET /runs``. O
    cancelamento é síncrono (só marca 'cancelled').
    """
    from backend.scheduling.background_tasks import (
        _get_run,
        cancel_background_run,
        get_task,
        resume_background_run,
    )

    await _require_thread_access(thread_id, request)
    run = await _get_run(run_id)
    if run is None or run.get("session_id") != thread_id:
        raise HTTPException(status_code=404, detail="Run não encontrada")
    task_id = run.get("task_id")
    task = await get_task(task_id) if isinstance(task_id, str) else None
    if (
        task is None
        or task.session_id != thread_id
        or task.user_id != _user_id(request)
    ):
        raise HTTPException(status_code=404, detail="Run não encontrada")
    if run.get("status") != "awaiting_approval":
        raise HTTPException(status_code=409, detail="Run não está aguardando aprovação")
    if body.decision == "cancel":
        await cancel_background_run(run_id)
        return {"status": "cancelled", "run_id": run_id}
    background_tasks.add_task(resume_background_run, run_id, body.decision)
    return {"status": "queued", "run_id": run_id}


def _row_to_run_out(r: dict[str, Any]) -> RunOut:
    return RunOut(
        id=r["id"],
        task_id=r["task_id"],
        run_thread_id=r.get("run_thread_id"),
        trigger_source=r["trigger_source"],
        status=r["status"],
        summary=r.get("summary"),
        started_at=r["started_at"],
        finished_at=r.get("finished_at"),
    )


@router.get("/runs", response_model=list[RunOut])
async def get_runs(request: Request, thread_id: str) -> list[RunOut]:
    from backend.scheduling.background_tasks import list_runs

    await _require_thread_access(thread_id, request)
    rows = await list_runs(thread_id)
    return [_row_to_run_out(r) for r in rows]


@router.get("/tasks/{task_id}/runs", response_model=list[RunOut])
async def get_task_runs(request: Request, thread_id: str, task_id: str) -> list[RunOut]:
    """Histórico de execuções de UMA task — fecha a conexão que faltava
    entre `GET /runs` (existia, só por session) e o card do Kanban."""
    from backend.scheduling.background_tasks import list_runs_for_task

    await _require_thread_access(thread_id, request)
    await _require_task(thread_id, task_id, request=request)
    rows = await list_runs_for_task(task_id)
    return [_row_to_run_out(r) for r in rows]


def _row_to_comment_out(r: dict[str, Any]) -> CommentOut:
    return CommentOut(
        id=r["id"],
        task_id=r["task_id"],
        user_id=r["user_id"],
        body=r["body"],
        created_at=r["created_at"],
    )


def _row_to_event_out(r: dict[str, Any]) -> TaskEventOut:
    return TaskEventOut(
        id=r["id"],
        task_id=r["task_id"],
        from_status=r.get("from_status"),
        to_status=r["to_status"],
        block_kind=r.get("block_kind"),
        block_reason=r.get("block_reason"),
        created_at=r["created_at"],
    )


@router.post("/tasks/{task_id}/comments", response_model=CommentOut, status_code=201)
async def post_comment_endpoint(
    request: Request, thread_id: str, task_id: str, body: CreateCommentRequest
) -> CommentOut:
    from backend.scheduling.kanban import add_comment

    uid = await _require_thread_access(thread_id, request)
    await _require_task(thread_id, task_id, request=request)
    try:
        comment = await add_comment(task_id, uid, body.body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("background: falha ao criar comentário")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return _row_to_comment_out(comment)


@router.get("/tasks/{task_id}/comments", response_model=list[CommentOut])
async def get_comments_endpoint(
    request: Request, thread_id: str, task_id: str
) -> list[CommentOut]:
    from backend.scheduling.kanban import list_comments

    await _require_thread_access(thread_id, request)
    await _require_task(thread_id, task_id, request=request)
    rows = await list_comments(task_id)
    return [_row_to_comment_out(r) for r in rows]


@router.get("/tasks/{task_id}/events", response_model=list[TaskEventOut])
async def get_events_endpoint(
    request: Request, thread_id: str, task_id: str
) -> list[TaskEventOut]:
    """Timeline de transições de status do card — `vectora_task_events`,
    gravada por `kanban._record_task_event` em cada transição."""
    from backend.scheduling.kanban import list_events

    await _require_thread_access(thread_id, request)
    await _require_task(thread_id, task_id, request=request)
    rows = await list_events(task_id)
    return [_row_to_event_out(r) for r in rows]
