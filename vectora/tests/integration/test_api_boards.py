"""Integração handler↔serviço de boards (multi-board).

Board é agrupamento nomeado por cima das tasks; session continua sendo o
contexto de execução. Coexiste com `/sessions/{thread_id}/background/*`
— não substitui essas rotas.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

import backend
from backend.api.handlers.background import CreateTaskRequest, post_task
from backend.api.handlers.boards import (
    CreateBoardRequest,
    UpdateBoardRequest,
    delete_board_endpoint,
    get_board_view,
    get_boards,
    patch_board,
    post_board,
)

_UUID = "aa844f17-7e0e-4b0a-8991-c3aab9bdcc63"
_OUTRO_UUID = "bb844f17-7e0e-4b0a-8991-c3aab9bdcc64"
_SCHEMA = (
    Path(backend.__file__).parent / "storage" / "migrations" / "sqlite" / "schema.sql"
)


def _req(uid: str | None = _UUID) -> Any:
    user = SimpleNamespace(id=uid) if uid is not None else None
    return SimpleNamespace(state=SimpleNamespace(user=user))


@pytest.fixture
async def db(tmp_path, monkeypatch):
    from backend.scheduling import background_tasks as bg

    db_path = str(tmp_path / "boards_api.db")
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
    await setup.commit()
    await setup.close()

    monkeypatch.setattr(bg, "_get_db", _connect)
    return db_path


async def test_post_board_cria_com_slug_derivado(db):
    board = await post_board(_req(), CreateBoardRequest(name="Board Alfa"))

    assert board.name == "Board Alfa"
    assert board.slug == "board-alfa"
    assert board.user_id == _UUID
    assert board.workspace_id is None


async def test_board_rejeita_chamada_sem_usuario(db):
    with pytest.raises(HTTPException) as exc_info:
        await get_boards(_req(uid=None))

    assert exc_info.value.status_code == 401


async def test_get_boards_so_lista_do_proprio_usuario(db):
    await post_board(_req(), CreateBoardRequest(name="Meu board"))
    await post_board(_req(_OUTRO_UUID), CreateBoardRequest(name="De outro"))

    boards = await get_boards(_req())

    assert [b.name for b in boards] == ["Meu board"]


async def test_patch_board_atualiza_e_recusa_dono_errado(db):
    board = await post_board(_req(), CreateBoardRequest(name="Original"))

    updated = await patch_board(_req(), board.id, UpdateBoardRequest(name="Renomeado"))
    assert updated.name == "Renomeado"

    # Erro/borda: dono errado → 404, não 403 — não vaza a existência do
    # board de outro usuário.
    with pytest.raises(HTTPException) as wrong_owner:
        await patch_board(
            _req(_OUTRO_UUID), board.id, UpdateBoardRequest(name="Roubado")
        )
    assert wrong_owner.value.status_code == 404


async def test_delete_board_vazio_e_recusa_board_com_tasks(db):
    vazio = await post_board(_req(), CreateBoardRequest(name="Vazio"))
    await delete_board_endpoint(_req(), vazio.id)
    assert await get_boards(_req()) == []

    com_tasks = await post_board(_req(), CreateBoardRequest(name="Com tasks"))
    await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine",
            name="x",
            instruction="i",
            trigger_type="manual",
            board_id=com_tasks.id,
        ),
    )

    # Erro/borda: 409 (conflito de estado), não 400 nem um delete que
    # também apaga as tasks — mover cards silenciosamente é irreversível
    # por acidente.
    with pytest.raises(HTTPException) as conflito:
        await delete_board_endpoint(_req(), com_tasks.id)
    assert conflito.value.status_code == 409


async def test_delete_board_dono_errado_e_404(db):
    board = await post_board(_req(), CreateBoardRequest(name="x"))

    with pytest.raises(HTTPException) as wrong_owner:
        await delete_board_endpoint(_req(_OUTRO_UUID), board.id)
    assert wrong_owner.value.status_code == 404


async def test_post_task_com_board_id_associa_e_aparece_no_board_view(db):
    board = await post_board(_req(), CreateBoardRequest(name="Sprint"))

    task = await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine",
            name="Tarefa do board",
            instruction="i",
            trigger_type="manual",
            board_id=board.id,
        ),
    )
    assert task.board_id == board.id

    view = await get_board_view(_req(), board.id)
    cartoes = [t.id for c in view.columns for t in c.tasks]
    assert task.id in cartoes


async def test_post_task_com_board_id_de_outro_usuario_e_404(db):
    """Erro/borda: sem essa checagem, um usuário conseguiria anexar uma
    task sua a um board que não é dele — vazamento de dado entre contas."""
    board_alheio = await post_board(_req(_OUTRO_UUID), CreateBoardRequest(name="x"))

    with pytest.raises(HTTPException) as wrong_owner:
        await post_task(
            _req(),
            "thread-1",
            CreateTaskRequest(
                kind="routine",
                name="x",
                instruction="i",
                trigger_type="manual",
                board_id=board_alheio.id,
            ),
        )
    assert wrong_owner.value.status_code == 404


async def test_get_board_view_agrupa_por_coluna_e_dono_errado_e_404(db):
    from backend.scheduling.kanban import set_status

    board = await post_board(_req(), CreateBoardRequest(name="Sprint"))
    todo = await post_task(
        _req(),
        "thread-1",
        CreateTaskRequest(
            kind="routine",
            name="a",
            instruction="i",
            trigger_type="manual",
            board_id=board.id,
        ),
    )
    await set_status(todo.id, "todo")

    from backend.scheduling.kanban import KANBAN_STATUSES

    view = await get_board_view(_req(), board.id)

    assert [c.status for c in view.columns] == list(KANBAN_STATUSES)
    by_status = {c.status: [t.id for t in c.tasks] for c in view.columns}
    assert by_status["todo"] == [todo.id]

    with pytest.raises(HTTPException) as wrong_owner:
        await get_board_view(_req(_OUTRO_UUID), board.id)
    assert wrong_owner.value.status_code == 404
