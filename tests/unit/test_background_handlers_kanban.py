"""`TaskOut` expõe o estado do Kanban (`status`/`block_kind`/`block_reason`)
via API REST, e o endpoint de desbloqueio reseta uma task bloqueada para
"ready", limpando o motivo do bloqueio.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import HTTPException, Request

import backend
from backend.api.handlers import background as bg_api
from backend.scheduling import background_tasks as bg
from backend.scheduling import kanban

_SCHEMA = (
    Path(backend.__file__).parent / "storage" / "migrations" / "sqlite" / "schema.sql"
)


@pytest.fixture
async def db(tmp_path, monkeypatch):
    import aiosqlite

    db_path = str(tmp_path / "bg.db")

    async def _connect() -> Any:
        conn: Any = await aiosqlite.connect(db_path)
        conn.row_factory = lambda c, r: dict(
            zip([col[0] for col in c.description], r, strict=False)
        )
        return conn

    setup = await _connect()
    await setup.executescript(_SCHEMA.read_text(encoding="utf-8"))
    await setup.commit()
    await setup.close()

    monkeypatch.setattr(bg, "_get_db", _connect)
    monkeypatch.setattr(kanban, "_get_db", _connect)
    return db_path


class _FakeRequestImpl:
    class _State:
        user = type("U", (), {"id": "u1"})()

    state = _State()


def _fake_request() -> Request:
    return cast("Request", _FakeRequestImpl())


@pytest.mark.asyncio
async def test_get_tasks_devolve_status_do_kanban(db):
    task = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="A",
        instruction="i",
        trigger_type="interval",
        trigger_config={"cron_expr": "0 9 * * *"},
    )

    saida = await bg_api.get_tasks(_fake_request(), "s1")

    assert len(saida) == 1
    assert saida[0].id == task.id
    # Task recorrente nasce com next_run_at futuro definido — status
    # "scheduled", não "ready" (reservado pra tasks já acionáveis agora,
    # como manual criada direto no board).
    assert saida[0].status == "scheduled"
    assert saida[0].block_kind is None


@pytest.mark.asyncio
async def test_get_tasks_devolve_priority_e_agent_profile_id(db):
    """`TaskOut` expõe tenant (workspace_id), assignee (agent_profile_id) e
    priority — os três campos do card do Kanban."""
    await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="subagent",
        name="A",
        instruction="i",
        trigger_type="manual",
        workspace_id="ws-1",
        agent_profile_id="profile-x",
        priority="high",
    )

    saida = await bg_api.get_tasks(_fake_request(), "s1")

    assert len(saida) == 1
    assert saida[0].workspace_id == "ws-1"
    assert saida[0].agent_profile_id == "profile-x"
    assert saida[0].priority == "high"


@pytest.mark.asyncio
async def test_post_task_aceita_priority_e_patch_atualiza(db):
    criada = await bg_api.post_task(
        _fake_request(),
        "s1",
        bg_api.CreateTaskRequest(
            kind="routine",
            name="A",
            instruction="i",
            trigger_type="manual",
            priority="urgent",
        ),
    )
    assert criada.priority == "urgent"

    atualizada = await bg_api.patch_task(
        _fake_request(),
        "s1",
        criada.id,
        bg_api.UpdateTaskRequest(priority="low"),
    )
    assert atualizada.priority == "low"


@pytest.mark.asyncio
async def test_unblock_endpoint_devolve_pra_ready_e_limpa_o_motivo(db):
    task = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="A",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )
    await kanban.block_task(task.id, "needs_input", "falta a chave da API")

    saida = await bg_api.unblock_task_endpoint(_fake_request(), "s1", task.id)

    assert saida.status == "ready"
    assert saida.block_kind is None
    assert saida.block_reason is None


@pytest.mark.asyncio
async def test_unblock_endpoint_com_task_de_outra_session_e_404(db):
    """Erro/borda: o mesmo isolamento por `thread_id` que os outros
    endpoints já aplicam (`_require_task`) precisa valer aqui também."""
    task = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="A",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )

    with pytest.raises(HTTPException) as exc_info:
        await bg_api.unblock_task_endpoint(_fake_request(), "sessao-errada", task.id)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_bulk_archive_processa_cada_item_isoladamente(db):
    """Feliz: arquiva as 3 tasks selecionadas. Erro/borda: um dos ids não
    existe — as outras 2 completam mesmo assim e a resposta reporta o
    erro por-item, sem abortar o lote inteiro."""
    tasks = [
        await bg.create_task(
            session_id="s1",
            user_id="u1",
            kind="routine",
            name=f"t{i}",
            instruction="i",
            trigger_type="manual",
            trigger_config={},
        )
        for i in range(3)
    ]
    task_ids = [t.id for t in tasks]
    body = bg_api.BulkTaskActionRequest(
        task_ids=[*task_ids, "id-inexistente"], action="archive"
    )

    saida = await bg_api.bulk_tasks_endpoint(_fake_request(), "s1", body)

    assert len(saida) == 4
    por_id = {r.task_id: r for r in saida}
    for task_id in task_ids:
        assert por_id[task_id].ok is True
        assert por_id[task_id].error is None
        atualizado = await bg.get_task(task_id)
        assert atualizado is not None
        assert atualizado.status == "archived"
    assert por_id["id-inexistente"].ok is False
    assert por_id["id-inexistente"].error is not None


@pytest.mark.asyncio
async def test_bulk_com_acao_desconhecida_e_recusado(db):
    """Erro/borda: ação fora de `_BULK_ACTIONS` é rejeitada antes de tocar
    em qualquer task — não existe ação parcial pra um lote inválido."""
    task = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="A",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )
    body = bg_api.BulkTaskActionRequest(task_ids=[task.id], action="deletar")

    with pytest.raises(HTTPException) as exc_info:
        await bg_api.bulk_tasks_endpoint(_fake_request(), "s1", body)

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_get_tasks_devolve_dependencies_com_contador_nm(db):
    """`TaskOut.dependencies` é a fonte real do contador N/M no card —
    antes o frontend declarava `blocked_by` mas nada preenchia."""
    pai_pronto = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="Pai concluído",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )
    pai_pendente = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="Pai pendente",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )
    filho = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="Filho",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )
    await kanban.set_status(pai_pronto.id, "done")
    await kanban.add_dependency(pai_pronto.id, filho.id)
    await kanban.add_dependency(pai_pendente.id, filho.id)

    saida = await bg_api.get_tasks(_fake_request(), "s1")
    filho_out = next(t for t in saida if t.id == filho.id)

    assert len(filho_out.dependencies) == 2
    concluidas = [d for d in filho_out.dependencies if d.status == "done"]
    assert len(concluidas) == 1
    assert {d.id for d in filho_out.dependencies} == {pai_pronto.id, pai_pendente.id}


@pytest.mark.asyncio
async def test_get_tasks_sem_dependencia_devolve_lista_vazia(db):
    """Edge: task sem nenhum pai em `vectora_task_links` não quebra — lista
    vazia, não `None`/erro."""
    await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="Sozinha",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )

    saida = await bg_api.get_tasks(_fake_request(), "s1")

    assert saida[0].dependencies == []


@pytest.mark.asyncio
async def test_get_task_runs_filtra_por_task_nao_por_session(db):
    """`GET /tasks/{id}/runs` — fecha a lacuna registrada no plano: só
    existia `list_runs` por session, sem filtro por card específico."""
    task_a = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="A",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )
    task_b = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="B",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )
    conn = await bg._get_db()
    for task, label in ((task_a, "run-a"), (task_b, "run-b")):
        await conn.execute(
            "INSERT INTO vectora_background_runs "
            "(id, task_id, session_id, trigger_source, status, started_at) "
            "VALUES (?, ?, ?, 'manual', 'done', datetime('now'))",
            (label, task.id, "s1"),
        )
    await conn.commit()
    await conn.close()

    saida = await bg_api.get_task_runs(_fake_request(), "s1", task_a.id)

    assert len(saida) == 1
    assert saida[0].id == "run-a"
    assert saida[0].task_id == task_a.id


@pytest.mark.asyncio
async def test_get_task_runs_task_de_outra_session_e_404(db):
    """Bad path: task existente mas de outra session não vaza run history —
    mesma checagem de `_require_task` já usada pelos demais endpoints."""
    task = await bg.create_task(
        session_id="s-outra",
        user_id="u1",
        kind="routine",
        name="A",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )

    with pytest.raises(HTTPException) as exc_info:
        await bg_api.get_task_runs(_fake_request(), "s1", task.id)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_post_comment_aparece_na_listagem_e_corpo_vazio_e_400(db):
    task = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="A",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )

    criado = await bg_api.post_comment_endpoint(
        _fake_request(), "s1", task.id, bg_api.CreateCommentRequest(body="oi")
    )
    listados = await bg_api.get_comments_endpoint(_fake_request(), "s1", task.id)

    assert criado.body == "oi"
    assert criado.user_id == "u1"
    assert len(listados) == 1
    assert listados[0].id == criado.id

    # Erro/borda: corpo vazio (ou whitespace) é recusado com 400, não 500.
    with pytest.raises(HTTPException) as exc_info:
        await bg_api.post_comment_endpoint(
            _fake_request(), "s1", task.id, bg_api.CreateCommentRequest(body="   ")
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_post_comment_task_inexistente_e_404_nao_500(db):
    with pytest.raises(HTTPException) as exc_info:
        await bg_api.post_comment_endpoint(
            _fake_request(),
            "s1",
            "id-inexistente",
            bg_api.CreateCommentRequest(body="oi"),
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_comments_task_de_outra_session_e_404(db):
    task = await bg.create_task(
        session_id="s-outra",
        user_id="u1",
        kind="routine",
        name="A",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )

    with pytest.raises(HTTPException) as exc_info:
        await bg_api.get_comments_endpoint(_fake_request(), "s1", task.id)

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_events_reflete_transicoes_reais_e_task_inexistente_e_404(db):
    task = await bg.create_task(
        session_id="s1",
        user_id="u1",
        kind="routine",
        name="A",
        instruction="i",
        trigger_type="manual",
        trigger_config={},
    )
    await kanban.set_status(task.id, "ready")
    await kanban.block_task(task.id, "needs_input", "falta chave")

    eventos = await bg_api.get_events_endpoint(_fake_request(), "s1", task.id)

    assert [e.to_status for e in eventos] == ["ready", "blocked"]
    assert eventos[-1].block_kind == "needs_input"

    with pytest.raises(HTTPException) as exc_info:
        await bg_api.get_events_endpoint(_fake_request(), "s1", "id-inexistente")
    assert exc_info.value.status_code == 404
