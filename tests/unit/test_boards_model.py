"""Multi-board — ``backend/scheduling/boards.py``.

Board é agrupamento NOMEADO por cima das tasks; a session continua sendo
o contexto de execução. Sem backfill em massa: ``board_id`` nullable
absorve tasks sem board associado, e o board "Default" nasce sob demanda.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import backend

_SCHEMA = (
    Path(backend.__file__).parent / "storage" / "migrations" / "sqlite" / "schema.sql"
)


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """Banco SQLite isolado com o schema real (não uma cópia reduzida) —
    ``boards.py`` depende de colunas/tabelas que só o schema.sql real tem."""
    import aiosqlite

    from backend.scheduling import background_tasks as bg

    db_path = str(tmp_path / "boards.db")
    up_sql = _SCHEMA.read_text(encoding="utf-8")

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


class TestCreateBoard:
    @pytest.mark.asyncio
    async def test_cria_com_slug_derivado_do_nome(self, db):
        from backend.scheduling.boards import create_board

        board = await create_board("u1", "Projeto Alpha")

        assert board.name == "Projeto Alpha"
        assert board.slug == "projeto-alpha"
        assert board.user_id == "u1"
        assert board.workspace_id is None
        assert board.archived_at is None

    @pytest.mark.asyncio
    async def test_nome_vazio_e_recusado(self, db):
        """Erro/borda: nome vazio (ou só espaço) não vira board sem
        identidade legível no switcher."""
        from backend.scheduling.boards import create_board

        with pytest.raises(ValueError, match="vazio"):
            await create_board("u1", "   ")

    @pytest.mark.asyncio
    async def test_slug_colidindo_ganha_sufixo_numerico(self, db):
        """Erro/borda: dois boards do MESMO usuário com nomes que geram o
        mesmo slug — UNIQUE(user_id, slug) do schema quebraria num INSERT
        cru; create_board precisa desambiguar antes."""
        from backend.scheduling.boards import create_board

        a = await create_board("u1", "Alfa")
        b = await create_board("u1", "Alfa")

        assert a.slug == "alfa"
        assert b.slug == "alfa-2"

    @pytest.mark.asyncio
    async def test_mesmo_slug_permitido_entre_usuarios_diferentes(self, db):
        """Erro/borda: UNIQUE é por (user_id, slug), não global — dois
        usuários podem ter um board "Default" cada, sem colisão."""
        from backend.scheduling.boards import create_board

        a = await create_board("u1", "Default")
        b = await create_board("u2", "Default")

        assert a.slug == b.slug == "default"


class TestListAndGetBoard:
    @pytest.mark.asyncio
    async def test_list_boards_so_do_usuario_e_em_ordem_de_criacao(self, db):
        from backend.scheduling.boards import create_board, list_boards

        await create_board("u1", "Primeiro")
        await create_board("u2", "De outro usuário")
        await create_board("u1", "Segundo")

        boards = await list_boards("u1")

        assert [b.name for b in boards] == ["Primeiro", "Segundo"]

    @pytest.mark.asyncio
    async def test_get_board_inexistente_devolve_none(self, db):
        from backend.scheduling.boards import get_board

        assert await get_board("nao-existe") is None


class TestUpdateBoard:
    @pytest.mark.asyncio
    async def test_atualiza_nome_e_workspace(self, db):
        from backend.scheduling.boards import create_board, update_board

        board = await create_board("u1", "Original")
        updated = await update_board(board.id, name="Renomeado", workspace_id="ws1")

        assert updated is not None
        assert updated.name == "Renomeado"
        assert updated.workspace_id == "ws1"

    @pytest.mark.asyncio
    async def test_nome_vazio_no_update_tambem_e_recusado(self, db):
        from backend.scheduling.boards import create_board, update_board

        board = await create_board("u1", "Original")

        with pytest.raises(ValueError, match="vazio"):
            await update_board(board.id, name="   ")

    @pytest.mark.asyncio
    async def test_update_de_board_inexistente_devolve_none_sem_lancar(self, db):
        from backend.scheduling.boards import update_board

        assert await update_board("nao-existe", name="x") is None


class TestDeleteBoard:
    @pytest.mark.asyncio
    async def test_apaga_board_vazio(self, db):
        from backend.scheduling.boards import create_board, delete_board, get_board

        board = await create_board("u1", "Vazio")

        assert await delete_board(board.id) is True
        assert await get_board(board.id) is None

    @pytest.mark.asyncio
    async def test_recusa_apagar_board_com_tasks(self, db):
        """Erro/borda: mover cards silenciosamente ou apagá-los junto é
        irreversível por acidente — o board com tasks precisa de 409, não
        de um DELETE que também leva as tasks embora."""
        from backend.scheduling import background_tasks as bg
        from backend.scheduling.boards import create_board, delete_board

        board = await create_board("u1", "Com tarefas")
        task = await bg.create_task(
            session_id="s1",
            user_id="u1",
            kind="routine",
            name="x",
            instruction="i",
            trigger_type="manual",
        )
        db_conn = await bg._get_db()
        await db_conn.execute(
            "UPDATE vectora_background_tasks SET board_id = ? WHERE id = ?",
            (board.id, task.id),
        )
        await db_conn.commit()

        with pytest.raises(ValueError, match="task"):
            await delete_board(board.id)

    @pytest.mark.asyncio
    async def test_delete_de_board_inexistente_devolve_false(self, db):
        from backend.scheduling.boards import delete_board

        assert await delete_board("nao-existe") is False


class TestDefaultBoard:
    @pytest.mark.asyncio
    async def test_cria_o_default_na_primeira_chamada_e_reusa_depois(self, db):
        from backend.scheduling.boards import get_or_create_default_board

        primeiro = await get_or_create_default_board("u1")
        segundo = await get_or_create_default_board("u1")

        assert primeiro.id == segundo.id
        assert primeiro.slug == "default"

    @pytest.mark.asyncio
    async def test_default_e_por_usuario(self, db):
        from backend.scheduling.boards import get_or_create_default_board

        a = await get_or_create_default_board("u1")
        b = await get_or_create_default_board("u2")

        assert a.id != b.id
