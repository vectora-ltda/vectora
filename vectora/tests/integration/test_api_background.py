"""Integração handler↔serviço de tarefas em segundo plano.

Exercita os endpoints session-scoped com um usuário de id **UUID** — o cenário
que antes derrubava o backend (`int(user.id)` em routines/heartbreak). Prova que
não há mais crash e que o disparo manual roda o agente, cria a run e registra a
thread.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import BackgroundTasks, HTTPException, Request

import backend
from backend.api.handlers import threads as thread_handler
from backend.api.handlers.background import (
    CreateLinkRequest,
    CreateTaskRequest,
    ResumeRunRequest,
    UpdateTaskRequest,
    add_link_endpoint,
    approve_review_endpoint,
    delete_task_endpoint,
    get_board,
    get_runs,
    get_tasks,
    patch_task,
    post_task,
    remove_link_endpoint,
    resume_run_endpoint,
    run_task_endpoint,
)
from backend.engine.hitl import ApprovalGate
from backend.persistence.native.session_store import SessionStore
from backend.scheduling import background_tasks as bg
from backend.services import agent_factory
from backend.services.agent_factory import NativeAgent
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.tools.registry import ToolRegistry
from backend.vtypes.message import VMessageChunk

_UUID = "aa844f17-7e0e-4b0a-8991-c3aab9bdcc63"
_OUTRO_UUID = "bb844f17-7e0e-4b0a-8991-c3aab9bdcc64"
_SCHEMA = (
    Path(backend.__file__).parent / "storage" / "migrations" / "sqlite" / "schema.sql"
)
_REAL_IS_THREAD_DELETED = thread_handler._is_thread_deleted


def _req(uid: str | None = _UUID) -> Any:
    user = SimpleNamespace(id=uid) if uid is not None else None
    return SimpleNamespace(state=SimpleNamespace(user=user))


@pytest.fixture
async def db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "bg.db")
    up_sql = _SCHEMA.read_text(encoding="utf-8")

    import aiosqlite

    async def _connect() -> Any:
        conn: Any = await aiosqlite.connect(db_path)
        conn.row_factory = lambda c, r: dict(
            zip([col[0] for col in c.description], r, strict=False)
        )
        return conn

    setup = await _connect()
    await setup.executescript(up_sql)
    await setup.execute(
        "CREATE TABLE IF NOT EXISTS deleted_threads ("
        "thread_id TEXT PRIMARY KEY, deleted_at TEXT NOT NULL)"
    )
    await setup.commit()
    await setup.close()

    monkeypatch.setattr(bg, "_get_db", _connect)

    async def _no_tombstone(_thread_id: str) -> bool:
        return False

    # This suite owns the background-task database; the production tombstone
    # database is covered by the focused regression below.
    monkeypatch.setattr(thread_handler, "_is_thread_deleted", _no_tombstone)

    class _SyntheticSessionStore:
        async def get_session(self, thread_id: str) -> dict[str, str]:
            return {"thread_id": thread_id, "user_id": _UUID}

    async def _get_synthetic_session_store() -> _SyntheticSessionStore:
        return _SyntheticSessionStore()

    monkeypatch.setattr(
        "backend.services.agent_factory.get_session_store",
        _get_synthetic_session_store,
    )
    return db_path


@pytest.fixture(autouse=True)
def _pro_tier_by_default(monkeypatch):
    """Tarefas `webhook` exigem tier pro — este arquivo cobre o handler
    REST, não o gating em si (coberto em test_services_background.py)."""
    monkeypatch.setenv("VECTORA_LICENSE_BYPASS", "1")


class _ScriptedChatClient:
    """Cliente de chat fake — devolve um único turno de texto puro (sem
    tool call), suficiente pra exercitar o handler REST ponta a ponta."""

    def __init__(self, texto: str) -> None:
        self._texto = texto

    async def astream(self, messages, *, tools=None, temperature=None, max_tokens=None):
        yield VMessageChunk(delta_text=self._texto)


def _patch_native_engine(
    monkeypatch, *, session_store: SessionStore, texto: str
) -> None:
    """Substitui as dependências que `run_task`/`resume_background_run`
    resolvem via `agent_factory` (motor nativo) + `FallbackChatClient` —
    mesmo padrão de `tests/unit/test_services_background.py`."""
    native_agent = NativeAgent(
        tool_registry=ToolRegistry(),
        subagent_catalog={},
        system_prompt="system prompt de teste",
    )

    async def _fake_get_native_agent(
        user_id: str | None = None,
        chat_mode: bool = False,
        workspace_id: str | None = None,
    ) -> NativeAgent:
        return native_agent

    async def _fake_get_session_store() -> SessionStore:
        return session_store

    approval_gate = ApprovalGate(session_store)

    async def _fake_get_approval_gate() -> ApprovalGate:
        return approval_gate

    async def _fake_get_store() -> None:
        return None

    monkeypatch.setattr(agent_factory, "get_native_agent", _fake_get_native_agent)
    monkeypatch.setattr(agent_factory, "get_session_store", _fake_get_session_store)
    monkeypatch.setattr(agent_factory, "get_approval_gate", _fake_get_approval_gate)
    monkeypatch.setattr(agent_factory, "get_store", _fake_get_store)
    monkeypatch.setattr(
        bg, "FallbackChatClient", lambda primary_model_id="": _ScriptedChatClient(texto)
    )


@pytest.fixture
async def native_session_store(tmp_path):
    """``SessionStore`` real (sqlite à parte do banco de tasks/runs) — a
    mesma infraestrutura que ``agent_factory.get_session_store()`` devolve
    em produção, isolada por teste."""
    pool = AsyncConnectionPool(
        str(tmp_path / "native-sessions.db"), min_size=1, max_size=2
    )
    await pool.open()
    store = SessionStore(pool)
    await store.setup()
    try:
        yield store
    finally:
        await pool.close()


async def test_post_task_with_uuid_user_does_not_crash(db):
    out = await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine",
            name="Resumo",
            instruction="Resuma o dia",
            trigger_type="interval",
            trigger_config={"cron_expr": "0 9 * * *"},
        ),
    )
    assert out.session_id == "thread-1"
    assert out.id  # criou sem ValueError de int(user.id)

    tasks = await get_tasks(_req(), "thread-1")
    assert [t.id for t in tasks] == [out.id]

    # Erro/borda: sem usuário autenticado → 401; cron inválido → 400.
    with pytest.raises(HTTPException) as no_auth:
        await get_tasks(_req(uid=None), "thread-1")
    assert no_auth.value.status_code == 401

    with pytest.raises(HTTPException) as bad_cron:
        await post_task(
            _req(),
            "thread-1",
            CreateTaskRequest(
                kind="routine",
                name="x",
                instruction="x",
                trigger_type="interval",
                trigger_config={"cron_expr": "nope"},
            ),
        )
    assert bad_cron.value.status_code == 400


async def test_post_task_aceita_agent_profile_id_do_formulario_de_criacao(db):
    """``create_task`` (backend/scheduling/background_tasks.py) já suporta
    ``agent_profile_id`` — só o schema HTTP não expõe o campo, então o
    formulário de nova tarefa (campo "assignee") não tinha
    como setar isso na criação. Regressão: sem o campo no schema, o assignee
    sempre volta ``None`` mesmo pedindo um perfil real."""
    out = await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine",
            name="Com assignee",
            instruction="Faça algo",
            trigger_type="manual",
            agent_profile_id="perfil-1",
        ),
    )
    assert out.agent_profile_id == "perfil-1"

    # Erro/borda: omitir o campo continua criando sem assignee (default None).
    sem_assignee = await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine",
            name="Sem assignee",
            instruction="Faça algo",
            trigger_type="manual",
        ),
    )
    assert sem_assignee.agent_profile_id is None


async def test_patch_and_delete_enforce_session_scope(db):
    out = await post_task(
        _req(),
        "thread-A",
        CreateTaskRequest(
            kind="heartbreak",
            name="ci",
            instruction="i",
            trigger_type="webhook",
            trigger_config={"provider": "github", "events": ["push"]},
        ),
    )
    updated = await patch_task(
        _req(), "thread-A", out.id, UpdateTaskRequest(enabled=False)
    )
    assert updated.enabled is False

    # Erro/borda: a task não pertence a outra session → 404.
    with pytest.raises(HTTPException) as wrong_session:
        await patch_task(_req(), "thread-B", out.id, UpdateTaskRequest(enabled=True))
    assert wrong_session.value.status_code == 404

    await delete_task_endpoint(_req(), "thread-A", out.id)
    assert await get_tasks(_req(), "thread-A") == []


async def test_background_tasks_reject_deleted_session_runs(
    db: str, monkeypatch: pytest.MonkeyPatch, native_session_store: SessionStore
) -> None:
    """Runs órfãs não podem ser consultadas após a sessão ser removida."""
    _patch_native_engine(monkeypatch, session_store=native_session_store, texto="feito")
    await native_session_store.create_session("deleted-session", user_id=_UUID)
    await native_session_store.delete_session("deleted-session")

    connection = await bg._get_db()
    try:
        await connection.execute(
            "INSERT INTO vectora_background_runs "
            "(id, task_id, session_id, trigger_source, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("orphan-run", "deleted-task", "deleted-session", "manual", "done"),
        )
        await connection.commit()
    finally:
        await connection.close()

    with pytest.raises(HTTPException) as exc_info:
        await get_runs(_req(), "deleted-session")

    assert exc_info.value.status_code == 404


async def test_background_task_owner_boundary_uses_same_session(
    db: str, monkeypatch: pytest.MonkeyPatch, native_session_store: SessionStore
) -> None:
    """A posse da task continua obrigatória quando a session coincide."""
    _patch_native_engine(monkeypatch, session_store=native_session_store, texto="feito")
    await native_session_store.create_session("thread-owner", user_id=_UUID)
    task = await bg.create_task(
        session_id="thread-owner",
        user_id=_OUTRO_UUID,
        kind="routine",
        name="privada",
        instruction="i",
        trigger_type="manual",
    )

    with pytest.raises(HTTPException) as exc_info:
        await get_tasks(_req(), "thread-owner")
    assert exc_info.value.status_code == 404


async def test_background_task_owner_can_list_owned_session(
    db: str, monkeypatch: pytest.MonkeyPatch, native_session_store: SessionStore
) -> None:
    """O dono pode listar tasks da própria session nativa."""
    _patch_native_engine(monkeypatch, session_store=native_session_store, texto="feito")
    await native_session_store.create_session("thread-owner-owned", user_id=_OUTRO_UUID)
    owned = await bg.create_task(
        session_id="thread-owner-owned",
        user_id=_OUTRO_UUID,
        kind="routine",
        name="privada do dono",
        instruction="i",
        trigger_type="manual",
    )
    tasks = await get_tasks(_req(_OUTRO_UUID), "thread-owner-owned")
    assert [item.id for item in tasks] == [owned.id]


async def test_post_task_registers_missing_native_session(
    db: str, monkeypatch: pytest.MonkeyPatch, native_session_store: SessionStore
) -> None:
    """A primeira task persiste a session gerada antes do primeiro chat."""
    _patch_native_engine(monkeypatch, session_store=native_session_store, texto="feito")
    thread_id = "thread-first-task"
    assert await native_session_store.get_session(thread_id) is None

    created = await post_task(
        _req(),
        thread_id,
        CreateTaskRequest(
            kind="routine",
            name="primeira task",
            instruction="i",
            trigger_type="manual",
        ),
    )

    session = await native_session_store.get_session(thread_id)
    assert session is not None
    assert session["user_id"] == _UUID
    assert created.session_id == thread_id
    assert [item.id for item in await get_tasks(_req(), thread_id)] == [created.id]


async def test_first_workspace_task_persists_workspace_on_session(
    db: str, monkeypatch: pytest.MonkeyPatch, native_session_store: SessionStore
) -> None:
    """A task can be the first operation without losing its workspace scope."""
    _patch_native_engine(monkeypatch, session_store=native_session_store, texto="feito")

    def _allow_workspace_access(workspace_id: str, request: Request) -> None:
        del workspace_id, request

    monkeypatch.setattr(
        "backend.api.handlers.workspaces.require_workspace_access",
        _allow_workspace_access,
    )
    thread_id = "thread-first-workspace-task"
    workspace_id = "workspace-first-task"

    created = await post_task(
        _req(),
        thread_id,
        CreateTaskRequest(
            kind="routine",
            name="primeira task no workspace",
            instruction="i",
            trigger_type="manual",
            workspace_id=workspace_id,
        ),
    )

    session = await native_session_store.get_session(thread_id)
    assert session is not None
    assert session["workspace_id"] == workspace_id
    assert created.workspace_id == workspace_id


async def test_post_task_cannot_recreate_tombstoned_session(
    db: str, monkeypatch: pytest.MonkeyPatch, native_session_store: SessionStore
) -> None:
    """A deleted ID cannot be reclaimed to reach retained task history."""
    _patch_native_engine(monkeypatch, session_store=native_session_store, texto="feito")
    thread_id = "thread-deleted-tombstone"
    import aiosqlite

    async def _connect_checkpoints() -> aiosqlite.Connection:
        conn: aiosqlite.Connection = await aiosqlite.connect(db)

        def _row_factory(
            cursor: sqlite3.Cursor, row: tuple[object, ...]
        ) -> dict[str, object]:
            return dict(zip([col[0] for col in cursor.description], row, strict=False))

        conn.row_factory = cast(
            "type",
            _row_factory,
        )
        return conn

    monkeypatch.setattr(thread_handler, "_get_db", _connect_checkpoints)
    monkeypatch.setattr(thread_handler, "_is_thread_deleted", _REAL_IS_THREAD_DELETED)
    checkpoints = await _connect_checkpoints()
    await checkpoints.execute(
        "INSERT INTO deleted_threads (thread_id, deleted_at) VALUES (?, ?)",
        (thread_id, "2026-09-21T00:00:00+00:00"),
    )
    await checkpoints.commit()
    await checkpoints.close()

    with pytest.raises(HTTPException) as exc_info:
        await post_task(
            _req(),
            thread_id,
            CreateTaskRequest(
                kind="routine",
                name="reclaim",
                instruction="i",
                trigger_type="manual",
            ),
        )

    assert exc_info.value.status_code == 404
    assert await native_session_store.get_session(thread_id) is None


async def test_patch_task_agent_profile_id_atribui_e_desatribui_via_http(db):
    """O drawer edita assignee depois da criação, campo
    novo em UpdateTaskRequest. Omitir o campo no PATCH não pode apagar um
    assignee já setado — só um PATCH que INCLUI o campo (mesmo como null)
    conta como intenção de mudar."""
    out = await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine",
            name="x",
            instruction="i",
            trigger_type="manual",
            agent_profile_id="perfil-1",
        ),
    )

    # PATCH que não menciona agent_profile_id (só troca o nome) preserva
    # o assignee existente — regressão clássica de "campo omitido apaga
    # tudo" se o handler filtrasse por is not None.
    so_nome = await patch_task(
        _req(), "thread-1", out.id, UpdateTaskRequest(name="renomeada")
    )
    assert so_nome.agent_profile_id == "perfil-1"

    # PATCH que inclui agent_profile_id=None desatribui de propósito.
    desatribuida = await patch_task(
        _req(), "thread-1", out.id, UpdateTaskRequest(agent_profile_id=None)
    )
    assert desatribuida.agent_profile_id is None


async def test_approve_review_endpoint_move_para_done(db):
    """Endpoint dedicado de aprovação, não a transição
    genérica de status (que recusa `review→done` de propósito)."""
    from backend.scheduling.kanban import set_status

    out = await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine", name="Revisar", instruction="i", trigger_type="manual"
        ),
    )
    await set_status(out.id, "review")

    approved = await approve_review_endpoint(_req(), "thread-1", out.id)

    assert approved.status == "done"

    # Erro/borda: aprovar de novo (já não está mais em review) → 400, não
    # um sucesso silencioso que reescreve o mesmo status.
    with pytest.raises(HTTPException) as again:
        await approve_review_endpoint(_req(), "thread-1", out.id)
    assert again.value.status_code == 400

    # Erro/borda: task de outra session → 404, mesmo enforcement de posse
    # que os outros endpoints de task já têm.
    with pytest.raises(HTTPException) as wrong_session:
        await approve_review_endpoint(_req(), "thread-B", out.id)
    assert wrong_session.value.status_code == 404


async def test_links_endpoint_adiciona_e_remove_dependencia(db):
    """`add_dependency` só era chamada internamente
    pela tool `kanban_decompose` do agente, sem rota HTTP nenhuma."""
    pai = await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine", name="Pai", instruction="i", trigger_type="manual"
        ),
    )
    filho = await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine", name="Filho", instruction="i", trigger_type="manual"
        ),
    )

    updated = await add_link_endpoint(
        _req(), "thread-1", filho.id, CreateLinkRequest(parent_id=pai.id)
    )
    assert [d.id for d in updated.dependencies] == [pai.id]

    updated = await remove_link_endpoint(_req(), "thread-1", filho.id, pai.id)
    assert updated.dependencies == []


async def test_links_endpoint_recusa_ciclo_com_409(db):
    """Erro/borda: fechar um ciclo pelo HTTP devolve 409 (conflito com o
    estado do grafo de dependências), não 500 nem um vínculo criado."""
    a = await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine", name="a", instruction="i", trigger_type="manual"
        ),
    )
    b = await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine", name="b", instruction="i", trigger_type="manual"
        ),
    )
    await add_link_endpoint(_req(), "thread-1", b.id, CreateLinkRequest(parent_id=a.id))

    with pytest.raises(HTTPException) as ciclo:
        await add_link_endpoint(
            _req(), "thread-1", a.id, CreateLinkRequest(parent_id=b.id)
        )
    assert ciclo.value.status_code == 409


async def test_links_endpoint_recusa_task_de_outra_session(db):
    """Erro/borda: linkar contra uma task de outra thread vazaria dado
    entre sessions — os dois lados do vínculo precisam pertencer à mesma."""
    out = await post_task(
        _req(),
        "thread-A",
        CreateTaskRequest(
            kind="routine", name="a", instruction="i", trigger_type="manual"
        ),
    )
    outra = await post_task(
        _req(),
        "thread-B",
        CreateTaskRequest(
            kind="routine", name="b", instruction="i", trigger_type="manual"
        ),
    )

    with pytest.raises(HTTPException) as wrong_session:
        await add_link_endpoint(
            _req(), "thread-A", out.id, CreateLinkRequest(parent_id=outra.id)
        )
    assert wrong_session.value.status_code == 404


async def test_task_out_expoe_progress_de_subtasks(db):
    """`TaskOut.progress` é `None` pra task folha e
    `{done, total}` real assim que ela ganha subtasks via links."""
    from backend.scheduling.kanban import set_status

    pai = await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine", name="Pai", instruction="i", trigger_type="manual"
        ),
    )
    assert pai.progress is None

    sub = await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine", name="Sub", instruction="i", trigger_type="manual"
        ),
    )
    await add_link_endpoint(
        _req(), "thread-1", sub.id, CreateLinkRequest(parent_id=pai.id)
    )
    await set_status(sub.id, "done")

    tasks = {t.id: t for t in await get_tasks(_req(), "thread-1")}
    pai_progress = tasks[pai.id].progress
    assert pai_progress is not None
    assert pai_progress.done == 1
    assert pai_progress.total == 1
    assert tasks[sub.id].progress is None


async def test_get_board_agrupa_por_coluna_na_ordem_canonica(db):
    """Só existia `GET /tasks` (lista plana); agrupar
    por coluna e contar comentários/progress exigia N chamadas
    client-side. `GET /board` devolve tudo numa passada."""
    from backend.scheduling.kanban import KANBAN_STATUSES, set_status

    todo = await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine", name="A fazer", instruction="i", trigger_type="manual"
        ),
    )
    await set_status(todo.id, "todo")  # create_task já entrega em "ready"
    pronta = await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine", name="Pronta", instruction="i", trigger_type="manual"
        ),
    )

    board = await get_board(_req(), "thread-1")

    # Ordem canônica (KANBAN_STATUSES), não a ordem de criação das tasks.
    assert [c.status for c in board.columns] == list(KANBAN_STATUSES)

    by_status = {c.status: [t.id for t in c.tasks] for c in board.columns}
    assert by_status["todo"] == [todo.id]
    assert by_status["ready"] == [pronta.id]
    assert by_status["done"] == []


async def test_get_board_expoe_tenants_e_assignees_distintos(db):
    """Erro/borda: tenants/assignees precisam ser distintos, não uma
    entrada por task — senão os dropdowns de filtro repetiriam o mesmo
    workspace/perfil uma vez por card."""
    await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine",
            name="a",
            instruction="i",
            trigger_type="manual",
            workspace_id="ws1",
            agent_profile_id="ap1",
        ),
    )
    await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine",
            name="b",
            instruction="i",
            trigger_type="manual",
            workspace_id="ws1",
            agent_profile_id="ap2",
        ),
    )

    board = await get_board(_req(), "thread-1")

    assert board.tenants == ["ws1"]
    assert board.assignees == ["ap1", "ap2"]


async def test_get_board_comment_count_e_progress_batelados_nao_por_card(db):
    """Prova que os agregados vêm em lote — não é só sobre corretude do
    valor (já coberto em test_kanban_model.py), é sobre o board devolver
    o mesmo dado que `GET /tasks` devolveria um por um, só que agrupado."""
    from backend.scheduling.kanban import add_comment, set_status

    pai = await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine", name="Pai", instruction="i", trigger_type="manual"
        ),
    )
    sub = await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine", name="Sub", instruction="i", trigger_type="manual"
        ),
    )
    await add_link_endpoint(
        _req(), "thread-1", sub.id, CreateLinkRequest(parent_id=pai.id)
    )
    await set_status(sub.id, "done")
    await add_comment(pai.id, "user-1", "comentário 1")
    await add_comment(pai.id, "user-1", "comentário 2")

    board = await get_board(_req(), "thread-1")
    cartao_pai = next(t for c in board.columns for t in c.tasks if t.id == pai.id)

    assert cartao_pai.comment_count == 2
    assert cartao_pai.progress is not None
    assert cartao_pai.progress.done == 1
    assert cartao_pai.progress.total == 1


async def test_manual_run_creates_run_and_registers_thread(
    db, monkeypatch, native_session_store
):
    _patch_native_engine(monkeypatch, session_store=native_session_store, texto="feito")
    await native_session_store.create_session("thread-run", user_id=_UUID)

    upserts: list[str] = []

    async def _upsert(thread_id, title=None, workspace_id=None):
        upserts.append(thread_id)

    monkeypatch.setattr("backend.api.handlers.threads._upsert_session", _upsert)

    created = await post_task(
        _req(),
        "thread-run",
        CreateTaskRequest(
            kind="routine",
            name="Check",
            instruction="Cheque",
            trigger_type="manual",
            trigger_config={},
        ),
    )

    bt = BackgroundTasks()
    resp = await run_task_endpoint(_req(), "thread-run", created.id, bt)
    assert resp["status"] == "queued"

    # Executa a task enfileirada (o que o FastAPI faria após a resposta).
    await bt()

    runs = await get_runs(_req(), "thread-run")
    assert len(runs) == 1
    assert runs[0].status == "done"
    assert runs[0].run_thread_id is not None
    assert upserts == [runs[0].run_thread_id]

    # Erro/borda: rodar task de outra session → 404.
    with pytest.raises(HTTPException) as wrong:
        await run_task_endpoint(_req(), "outra-thread", created.id, BackgroundTasks())
    assert wrong.value.status_code == 404


async def test_manual_run_com_subagent_type_roda_a_soul_ate_o_fim(
    db, monkeypatch, native_session_store
):
    """Gap real (revisão de 2026-08-30): nenhum teste de integração criava
    uma task com `trigger_config={"subagent_type": ...}` pelo endpoint REST
    de verdade (`post_task`) — só via `bg.create_task` direto em unit. Fecha
    o ciclo completo: POST /tasks → POST /tasks/{id}/run → task executa a
    SOUL pedida até `status == "done"`, mesmo caminho que
    `test_manual_run_creates_run_and_registers_thread` já prova pro caso
    sem `subagent_type`."""
    _patch_native_engine(monkeypatch, session_store=native_session_store, texto="feito")
    await native_session_store.create_session("thread-run-soul", user_id=_UUID)

    created = await post_task(
        _req(),
        "thread-run-soul",
        CreateTaskRequest(
            kind="routine",
            name="Delegar pro coder",
            instruction="corrigir o bug",
            trigger_type="manual",
            trigger_config={"subagent_type": "coder"},
        ),
    )

    bt = BackgroundTasks()
    resp = await run_task_endpoint(_req(), "thread-run-soul", created.id, bt)
    assert resp["status"] == "queued"
    await bt()

    runs = await get_runs(_req(), "thread-run-soul")
    assert len(runs) == 1
    assert runs[0].status == "done"
    assert runs[0].run_thread_id is not None


async def test_resume_run_endpoint_cancel_and_approve(
    db, monkeypatch, native_session_store
):
    """POST /runs/{id}/resume: cancela sincronamente (decision='cancel') ou
    enfileira o resume (approve/reject) via BackgroundTasks — mesmo padrão do
    disparo manual. Erro/borda: run inexistente → 404; run que não está
    aguardando aprovação → 409."""
    _patch_native_engine(monkeypatch, session_store=native_session_store, texto="feito")
    await native_session_store.create_session("thread-resume", user_id=_UUID)
    created = await post_task(
        _req(),
        "thread-resume",
        CreateTaskRequest(
            kind="routine",
            name="Perigosa",
            instruction="i",
            trigger_type="manual",
            trigger_config={"permission_mode": "ask"},
        ),
    )

    # Erro/borda: run inexistente → 404.
    with pytest.raises(HTTPException) as not_found:
        await resume_run_endpoint(
            _req(), "thread-resume", "nao-existe", ResumeRunRequest(), BackgroundTasks()
        )
    assert not_found.value.status_code == 404

    task = await bg.get_task(created.id)
    assert task is not None

    # Registra uma run pausada em HITL.
    run_id = "run-http-resume"
    await bg._insert_run(run_id, task, "bg-thread-resume", "manual")

    # Erro/borda: run 'running' (ainda não pausou) → 409.
    with pytest.raises(HTTPException) as not_awaiting:
        await resume_run_endpoint(
            _req(), "thread-resume", run_id, ResumeRunRequest(), BackgroundTasks()
        )
    assert not_awaiting.value.status_code == 409

    await bg._mark_run_awaiting(run_id, "Aguardando aprovação: terminal")

    # decision='cancel' — síncrono, run vira 'cancelled' imediatamente.
    resp_cancel = await resume_run_endpoint(
        _req(),
        "thread-resume",
        run_id,
        ResumeRunRequest(decision="cancel"),
        BackgroundTasks(),
    )
    assert resp_cancel == {"status": "cancelled", "run_id": run_id}
    cancelled = await bg._get_run(run_id)
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"

    # decision='approve' num run pendente — enfileira via BackgroundTasks.
    run_id_2 = "run-http-approve"
    await bg._insert_run(run_id_2, task, "bg-thread-resume-2", "manual")
    await bg._mark_run_awaiting(run_id_2, "Aguardando aprovação: terminal")

    _patch_native_engine(
        monkeypatch, session_store=native_session_store, texto="concluído"
    )
    # `resume_conversation` só age se houver uma aprovação pendente real no
    # SessionStore pro `run_thread_id` — registra a mesma pendência que uma
    # pausa HITL real teria persistido antes do run virar 'awaiting_approval'.
    await native_session_store.create_session(
        "bg-thread-resume-2", user_id=task.user_id
    )
    await native_session_store.put_pending_approval(
        "bg-thread-resume-2",
        interrupt_id="intr-resume-2",
        tool_name="terminal",
        tool_call_id="call-1",
        args={"command": "echo oi"},
    )

    bt = BackgroundTasks()
    resp_approve = await resume_run_endpoint(
        _req(), "thread-resume", run_id_2, ResumeRunRequest(decision="approve"), bt
    )
    assert resp_approve == {"status": "queued", "run_id": run_id_2}

    await bt()  # executa o resume enfileirado (o que o FastAPI faria depois)

    approved = await bg._get_run(run_id_2)
    assert approved is not None
    assert approved["status"] == "done"
