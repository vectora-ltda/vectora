"""Tools de agente para o board Kanban (`backend/tools/kanban.py`).

`kanban_create`/`kanban_update_status` mutam `vectora_background_tasks` via
`backend.scheduling.background_tasks`/`backend.scheduling.kanban` — nunca por
SQL direto. `kanban_list` é só leitura. Cada caminho feliz tem o par de
erro/borda no mesmo teste.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
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

    @pytest.mark.asyncio
    async def test_run_de_background_pode_atualizar_a_propria_task(
        self, db: str
    ) -> None:
        from backend.tools.kanban import kanban_create, kanban_update_status

        created = json.loads(
            await kanban_create(ctx=_ctx(), name="card", instruction="faça algo")
        )
        task_id = created["task_id"]
        assert await kanban.claim_task(task_id, "run-1")

        out = json.loads(
            await kanban_update_status(
                ctx=ToolContext(
                    thread_id="bg-run",
                    background_task_id=task_id,
                    background_run_id="run-1",
                ),
                task_id=task_id,
                status="blocked",
                block_kind="capability",
                block_reason="falta ferramenta",
            )
        )

        assert out["result"] == "ok"
        assert (await kanban.get_task_status(task_id))["status"] == "blocked"

    @pytest.mark.asyncio
    async def test_run_de_background_nao_pode_atualizar_outra_task(
        self, db: str
    ) -> None:
        from backend.tools.kanban import kanban_create, kanban_update_status

        created = json.loads(
            await kanban_create(ctx=_ctx(), name="card", instruction="faça algo")
        )

        out = json.loads(
            await kanban_update_status(
                ctx=ToolContext(
                    thread_id="bg-run",
                    background_task_id="other-task",
                    background_run_id="run-1",
                ),
                task_id=created["task_id"],
                status="triage",
            )
        )

        assert out["status"] == "error"
        assert "não corresponde" in out["error"]
        assert (await kanban.get_task_status(created["task_id"]))["status"] == "ready"

    @pytest.mark.asyncio
    async def test_run_de_background_expirado_nao_pode_alterar_claim_retomado(
        self, db: str
    ) -> None:
        from backend.tools.kanban import kanban_create, kanban_update_status

        created = json.loads(
            await kanban_create(ctx=_ctx(), name="card", instruction="faça algo")
        )
        task_id = created["task_id"]
        assert await kanban.claim_task(task_id, "run-1")

        conn = await kanban._get_db()
        await conn.execute(
            "UPDATE vectora_background_tasks SET claim_lock = ? WHERE id = ?",
            ("run-2", task_id),
        )
        await conn.commit()
        await conn.close()

        out = json.loads(
            await kanban_update_status(
                ctx=ToolContext(
                    thread_id="bg-run",
                    background_task_id=task_id,
                    background_run_id="run-1",
                ),
                task_id=task_id,
                status="blocked",
                block_kind="capability",
                block_reason="não deve passar",
            )
        )

        assert out["status"] == "error"
        assert "claim atual" in out["error"]

    @pytest.mark.asyncio
    async def test_resume_renova_claim_mesmo_ainda_valido(self, db: str) -> None:
        from backend.tools.kanban import kanban_create

        created = json.loads(
            await kanban_create(ctx=_ctx(), name="card", instruction="faça algo")
        )
        task_id = created["task_id"]
        assert await kanban.claim_task(task_id, "run-1", ttl_s=2)

        conn = await kanban._get_db()
        quase_expirado = (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
        await conn.execute(
            "UPDATE vectora_background_tasks SET claim_expires_at = ? WHERE id = ?",
            (quase_expirado, task_id),
        )
        await conn.commit()
        await conn.close()

        assert await kanban.ensure_task_claim(task_id, "run-1", ttl_s=900)
        conn = await kanban._get_db()
        async with conn.execute(
            "SELECT claim_expires_at FROM vectora_background_tasks WHERE id = ?",
            (task_id,),
        ) as cur:
            row = await cur.fetchone()
        await conn.close()
        assert datetime.fromisoformat(row["claim_expires_at"]) > datetime.now(
            UTC
        ) + timedelta(seconds=100)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("task_id", "run_id"),
        [
            (None, "run-1"),
            ("", "run-1"),
            ("missing", None),
            ("missing", ""),
        ],
    )
    async def test_resume_recusa_ids_ausentes_ou_vazios(
        self, db: str, task_id: str | None, run_id: str | None
    ) -> None:
        assert not await kanban.ensure_task_claim(
            cast("str", task_id), cast("str", run_id)
        )

    @pytest.mark.asyncio
    async def test_write_de_background_recusa_claim_perdido_entre_precheck_e_update(
        self, db: str
    ) -> None:
        from backend.tools.kanban import kanban_create

        created = json.loads(
            await kanban_create(ctx=_ctx(), name="card", instruction="faça algo")
        )
        task_id = created["task_id"]
        assert await kanban.claim_task(task_id, "run-1")
        conn = await kanban._get_db()
        await conn.execute(
            "UPDATE vectora_background_tasks SET claim_lock = ? WHERE id = ?",
            ("run-2", task_id),
        )
        await conn.commit()
        await conn.close()

        with pytest.raises(ValueError, match="perdeu o claim"):
            await kanban.block_task(
                task_id,
                "capability",
                "não deve passar",
                authorized_run_id="run-1",
            )

    @pytest.mark.asyncio
    async def test_ready_respeita_transicao_e_limpa_bloqueio(self, db: str) -> None:
        from backend.tools.kanban import kanban_create, kanban_update_status

        created = json.loads(
            await kanban_create(ctx=_ctx(), name="card", instruction="faça algo")
        )
        task_id = created["task_id"]
        await kanban_update_status(
            ctx=_ctx(),
            task_id=task_id,
            status="blocked",
            block_kind="capability",
            block_reason="falta ferramenta",
        )

        out = json.loads(
            await kanban_update_status(ctx=_ctx(), task_id=task_id, status="ready")
        )

        assert out["result"] == "ok"
        estado = await kanban.get_task_status(task_id)
        assert estado["status"] == "ready"
        assert estado["block_kind"] is None
        assert estado["block_reason"] is None

    @pytest.mark.asyncio
    async def test_done_nao_pode_voltar_direto_para_ready(self, db: str) -> None:
        from backend.tools.kanban import kanban_create, kanban_update_status

        created = json.loads(
            await kanban_create(ctx=_ctx(), name="card", instruction="faça algo")
        )
        task_id = created["task_id"]
        await kanban.set_status(task_id, "done")

        out = json.loads(
            await kanban_update_status(ctx=_ctx(), task_id=task_id, status="ready")
        )

        assert out["status"] == "error"
        assert "transição 'done' → 'ready'" in out["error"]
        assert (await kanban.get_task_status(task_id))["status"] == "done"

    @pytest.mark.asyncio
    async def test_run_de_background_com_id_vazio_usa_sessao_normal(
        self, db: str
    ) -> None:
        from backend.tools.kanban import kanban_create, kanban_update_status

        created = json.loads(
            await kanban_create(ctx=_ctx(), name="card", instruction="faça algo")
        )

        out = json.loads(
            await kanban_update_status(
                ctx=ToolContext(thread_id="s1", user_id="u1", background_task_id=""),
                task_id=created["task_id"],
                status="triage",
            )
        )

        assert out["result"] == "ok"
        assert (await kanban.get_task_status(created["task_id"]))["status"] == "triage"


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
