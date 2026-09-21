"""Tools de agente para o board Kanban (`backend/tools/kanban.py`).

`kanban_create`/`kanban_update_status` mutam `vectora_background_tasks` via
`backend.scheduling.background_tasks`/`backend.scheduling.kanban` — nunca por
SQL direto. `kanban_list` é só leitura. Cada caminho feliz tem o par de
erro/borda no mesmo teste.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

import backend
from backend.scheduling import background_tasks as bg
from backend.scheduling import kanban
from backend.tools.context import ToolContext
from backend.vtypes.message import ContentBlock, MessageRole, VMessage


def _fake_response(texto: str) -> VMessage:
    return VMessage(
        role=MessageRole.ASSISTANT, content=[ContentBlock(kind="text", text=texto)]
    )


_SCHEMA = (
    Path(backend.__file__).parent / "storage" / "migrations" / "sqlite" / "schema.sql"
)


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """Banco SQLite temporário com o schema real aplicado.

    `_get_db` abre uma conexão nova por chamada; todas apontam pro mesmo
    arquivo, então o estado persiste entre operações — igual à produção.
    """
    db_path = str(tmp_path / "kanban_tools.db")
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


def _ctx(session_id: str = "s1", user_id: str = "u1") -> ToolContext:
    return ToolContext(thread_id=session_id, user_id=user_id)


class TestKanbanCreate:
    @pytest.mark.asyncio
    async def test_cria_card_e_aparece_no_board(self, db):
        from backend.tools.kanban import kanban_create

        out = json.loads(
            await kanban_create(
                ctx=_ctx(),
                name="Revisar PR #42",
                instruction="Revise o PR e comente achados.",
            )
        )
        assert out["status"] == "created"
        assert out["task_id"]

        task = await bg.get_task(out["task_id"])
        assert task is not None
        assert task.name == "Revisar PR #42"
        assert task.trigger_type == "manual"

    @pytest.mark.asyncio
    async def test_nome_ou_instrucao_vazios_retornam_erro_tipado_sem_lancar(self, db):
        """Erro/borda: parâmetro inválido não lança — vira erro tipado, e
        nenhuma linha é gravada no board."""
        from backend.tools.kanban import kanban_create

        out_nome = json.loads(
            await kanban_create(ctx=_ctx(), name="  ", instruction="faça algo")
        )
        assert out_nome["status"] == "error"

        out_instrucao = json.loads(
            await kanban_create(ctx=_ctx(), name="card", instruction="  ")
        )
        assert out_instrucao["status"] == "error"

        assert await bg.list_tasks("s1") == []


class TestKanbanUpdateStatus:
    @pytest.mark.asyncio
    async def test_move_card_para_status_valido(self, db):
        from backend.tools.kanban import kanban_create, kanban_update_status

        created = json.loads(
            await kanban_create(ctx=_ctx(), name="card", instruction="faça algo")
        )
        task_id = created["task_id"]

        out = json.loads(
            await kanban_update_status(ctx=_ctx(), task_id=task_id, status="triage")
        )
        assert out["result"] == "ok"

        estado = await kanban.get_task_status(task_id)
        assert estado["status"] == "triage"

    @pytest.mark.asyncio
    async def test_status_fora_da_taxonomia_retorna_erro_tipado_sem_lancar(self, db):
        """Erro/borda: `set_status` de baixo nível recusa a transição — o
        teste confirma que o erro chega tratado até o topo (a tool nunca
        propaga a `ValueError` crua)."""
        from backend.tools.kanban import kanban_create, kanban_update_status

        created = json.loads(
            await kanban_create(ctx=_ctx(), name="card", instruction="faça algo")
        )
        task_id = created["task_id"]

        out = json.loads(
            await kanban_update_status(ctx=_ctx(), task_id=task_id, status="em-analise")
        )
        assert out["status"] == "error"
        assert "em-analise" in out["error"]

        # Nada mudou — o card continua na coluna original.
        estado = await kanban.get_task_status(task_id)
        assert estado["status"] != "em-analise"

    @pytest.mark.asyncio
    async def test_status_blocked_passa_por_block_task_com_kind_tipado(self, db):
        from backend.tools.kanban import kanban_create, kanban_update_status

        created = json.loads(
            await kanban_create(ctx=_ctx(), name="card", instruction="faça algo")
        )
        task_id = created["task_id"]

        out = json.loads(
            await kanban_update_status(
                ctx=_ctx(),
                task_id=task_id,
                status="blocked",
                block_kind="capability",
                block_reason="falta ferramenta X",
            )
        )
        assert out["result"] == "ok"

        estado = await kanban.get_task_status(task_id)
        assert estado["status"] == "blocked"
        assert estado["block_kind"] == "capability"


class TestKanbanList:
    @pytest.mark.asyncio
    async def test_lista_cards_reais_da_sessao(self, db):
        from backend.tools.kanban import kanban_create, kanban_list

        await kanban_create(ctx=_ctx(), name="card 1", instruction="faça algo")
        await kanban_create(ctx=_ctx(), name="card 2", instruction="faça outra coisa")

        out = json.loads(await kanban_list(ctx=_ctx()))
        assert out["status"] == "ok"
        assert {c["name"] for c in out["cards"]} == {"card 1", "card 2"}

    @pytest.mark.asyncio
    async def test_board_vazio_retorna_lista_vazia_sem_erro(self, db):
        """Erro/borda: sessão sem nenhum card não é uma falha — a coluna
        simplesmente vem vazia."""
        from backend.tools.kanban import kanban_list

        out = json.loads(await kanban_list(ctx=_ctx("sessao-nova")))
        assert out["status"] == "ok"
        assert out["cards"] == []

    @pytest.mark.asyncio
    async def test_filtra_por_status(self, db):
        from backend.tools.kanban import (
            kanban_create,
            kanban_list,
            kanban_update_status,
        )

        created = json.loads(
            await kanban_create(ctx=_ctx(), name="card review", instruction="faça algo")
        )
        await kanban_create(ctx=_ctx(), name="card ready", instruction="faça algo")
        await kanban_update_status(
            ctx=_ctx(), task_id=created["task_id"], status="triage"
        )

        out = json.loads(await kanban_list(ctx=_ctx(), status="triage"))
        assert [c["name"] for c in out["cards"]] == ["card review"]

    @pytest.mark.asyncio
    async def test_recusa_task_de_outra_sessao(self, db):
        from backend.tools.kanban import kanban_create, kanban_update_status

        created = json.loads(
            await kanban_create(
                ctx=_ctx("sessao-dona"), name="card", instruction="faça algo"
            )
        )

        out = json.loads(
            await kanban_update_status(
                ctx=_ctx("sessao-alheia"),
                task_id=created["task_id"],
                status="review",
            )
        )

        assert out["status"] == "error"
        assert "não pertence" in out["error"]
        assert (await kanban.get_task_status(created["task_id"]))["status"] == "ready"

    @pytest.mark.asyncio
    async def test_nao_permite_agente_forcar_running_ou_done(self, db):
        from backend.tools.kanban import kanban_create, kanban_update_status

        created = json.loads(
            await kanban_create(ctx=_ctx(), name="card", instruction="faça algo")
        )

        for status in ("running", "done"):
            out = json.loads(
                await kanban_update_status(
                    ctx=_ctx(), task_id=created["task_id"], status=status
                )
            )
            assert out["status"] == "error"
            assert "fluxos de execução" in out["error"]

        assert (await kanban.get_task_status(created["task_id"]))["status"] == "ready"


class TestKanbanDecompose:
    @pytest.mark.asyncio
    async def test_decompoe_triage_em_children_com_dependencia(self, db):
        from backend.tools.kanban import kanban_create, kanban_decompose

        created = json.loads(
            await kanban_create(
                ctx=_ctx(),
                name="Migrar auth",
                instruction="Migre o módulo de auth pra OIDC",
            )
        )
        task_id = created["task_id"]
        await kanban.set_status(task_id, "triage")

        grafo = json.dumps(
            [
                {
                    "name": "Escrever cliente OIDC",
                    "instruction": "Implemente o client",
                    "parents": [],
                },
                {
                    "name": "Migrar endpoints",
                    "instruction": "Troque os handlers",
                    "parents": [0],
                },
            ]
        )
        fake_agenerate = AsyncMock(return_value=_fake_response(grafo))
        with patch(
            "backend.llm.fallback_chat_client.FallbackChatClient.agenerate",
            fake_agenerate,
        ):
            out = json.loads(await kanban_decompose(ctx=_ctx(), task_id=task_id))

        assert out["status"] == "ok"
        assert out["decomposed"] is True
        assert len(out["children"]) == 2

        pai_id, filho_id = out["children"]
        deps = await kanban.get_dependencies(filho_id)
        assert [d["id"] for d in deps] == [pai_id]

        estado_filho = await kanban.get_task_status(filho_id)
        assert estado_filho["status"] == "todo"
        estado_pai = await kanban.get_task_status(pai_id)
        assert estado_pai["status"] == "ready"

        estado_original = await kanban.get_task_status(task_id)
        assert estado_original["status"] == "archived"

    @pytest.mark.asyncio
    async def test_resposta_malformada_do_modelo_nao_quebra_e_mantem_triage(self, db):
        """Erro/borda: JSON inválido, `parents` apontando pra índice
        inexistente ou grafo vazio nunca derrubam a tool — o card original
        segue em `triage` sem nenhum child criado."""
        from backend.tools.kanban import kanban_create, kanban_decompose

        created = json.loads(
            await kanban_create(ctx=_ctx(), name="card", instruction="faça algo")
        )
        task_id = created["task_id"]
        await kanban.set_status(task_id, "triage")

        fake_agenerate = AsyncMock(return_value=_fake_response("isso não é JSON"))
        with patch(
            "backend.llm.fallback_chat_client.FallbackChatClient.agenerate",
            fake_agenerate,
        ):
            out = json.loads(await kanban_decompose(ctx=_ctx(), task_id=task_id))

        assert out["status"] == "ok"
        assert out["decomposed"] is False

        estado = await kanban.get_task_status(task_id)
        assert estado["status"] == "triage"
        assert [t.id for t in await bg.list_tasks("s1")] == [task_id]

    @pytest.mark.asyncio
    async def test_ciclo_proposto_pelo_modelo_e_ignorado_sem_quebrar_os_demais(
        self, db
    ):
        """Erro/borda: um nó que se autorreferencia como pai (`parents: [1]`
        no próprio índice 1) é descartado nó a nó — o resto do grafo continua
        criado normalmente."""
        from backend.tools.kanban import kanban_create, kanban_decompose

        created = json.loads(
            await kanban_create(ctx=_ctx(), name="card", instruction="faça algo")
        )
        task_id = created["task_id"]
        await kanban.set_status(task_id, "triage")

        grafo = json.dumps(
            [
                {"name": "nó A", "instruction": "faça A", "parents": []},
                {"name": "nó B (autoref)", "instruction": "faça B", "parents": [1]},
            ]
        )
        fake_agenerate = AsyncMock(return_value=_fake_response(grafo))
        with patch(
            "backend.llm.fallback_chat_client.FallbackChatClient.agenerate",
            fake_agenerate,
        ):
            out = json.loads(await kanban_decompose(ctx=_ctx(), task_id=task_id))

        assert out["decomposed"] is True
        assert len(out["children"]) == 2
        no_b_id = out["children"][1]
        estado_b = await kanban.get_task_status(no_b_id)
        assert estado_b["status"] == "ready"

    @pytest.mark.asyncio
    async def test_task_fora_de_triage_retorna_erro_tipado_sem_chamar_llm(self, db):
        from backend.tools.kanban import kanban_create, kanban_decompose

        created = json.loads(
            await kanban_create(ctx=_ctx(), name="card", instruction="faça algo")
        )
        task_id = created["task_id"]

        fake_agenerate = AsyncMock(
            side_effect=AssertionError("não deveria chamar o LLM")
        )
        with patch(
            "backend.llm.fallback_chat_client.FallbackChatClient.agenerate",
            fake_agenerate,
        ):
            out = json.loads(await kanban_decompose(ctx=_ctx(), task_id=task_id))

        assert out["status"] == "error"
        assert "triage" in out["error"]
        fake_agenerate.assert_not_called()
