"""``SessionStore`` — persistência simplificada de sessões/mensagens.
Fixture com pool real sobre `tmp_path`, sem mock."""

from __future__ import annotations

import asyncio

import pytest

from backend.persistence.native.session_store import SessionStore
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.vtypes.message import (
    ContentBlock,
    MessageRole,
    ToolCall,
    VMessage,
    text_message,
)


@pytest.fixture
async def store(tmp_path):
    pool = AsyncConnectionPool(str(tmp_path / "sessions.db"), min_size=1, max_size=2)
    await pool.open()
    store = SessionStore(pool)
    await store.setup()
    try:
        yield store
    finally:
        await pool.close()


class TestCreateSession:
    async def test_cria_sessao_com_campos_obrigatorios(self, store: SessionStore):
        await store.create_session("thread-1", user_id="alice")

        async with store._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT user_id, mode, permission_mode FROM sessions WHERE thread_id = ?",
                ("thread-1",),
            )
            row = await cur.fetchone()

        assert row is not None
        assert tuple(row) == ("alice", "chat", "ask")

    async def test_criar_duas_vezes_a_mesma_thread_nao_falha(self, store: SessionStore):
        await store.create_session("thread-1", user_id="alice")
        await store.create_session("thread-1", user_id="alice")  # idempotente

        async with store._pool.acquire() as conn:
            cur = await conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE thread_id = ?", ("thread-1",)
            )
            fetched = await cur.fetchone()
            assert fetched is not None
            (total,) = fetched

        assert total == 1


class TestAppendMessageEGetHistory:
    async def test_lista_compara_e_seleciona_branches(self, store: SessionStore):
        await store.create_session("thread-branches", user_id="alice")
        root = await store.append_message(
            "thread-branches", text_message(MessageRole.USER, "pergunta")
        )
        old_head = await store.append_message(
            "thread-branches",
            text_message(MessageRole.ASSISTANT, "resposta antiga"),
            parent_message_id=root,
        )
        new_head = await store.append_message(
            "thread-branches",
            text_message(MessageRole.ASSISTANT, "resposta nova"),
            parent_message_id=root,
        )

        branches = await store.list_branch_heads("thread-branches")
        assert {item["head_message_id"] for item in branches} == {old_head, new_head}
        comparison = await store.compare_branches("thread-branches", old_head)
        assert comparison["common_message_ids"] == [root]
        assert comparison["selected_divergent_message_ids"] == [old_head]
        await store.set_branch_head("thread-branches", old_head)
        assert await store.get_branch_head_id("thread-branches") == old_head

    async def test_rejeita_selecao_de_mensagem_intermediaria(self, store: SessionStore):
        await store.create_session("thread-branches", user_id="alice")
        root = await store.append_message(
            "thread-branches", text_message(MessageRole.USER, "pergunta")
        )
        await store.append_message(
            "thread-branches",
            text_message(MessageRole.ASSISTANT, "resposta"),
            parent_message_id=root,
        )

        with pytest.raises(ValueError, match="não pertence"):
            await store.set_branch_head("thread-branches", root)

    async def test_round_trip_preserva_ordem_e_conteudo(self, store: SessionStore):
        await store.create_session("thread-1", user_id="alice")
        id1 = await store.append_message(
            "thread-1", text_message(MessageRole.USER, "oi")
        )
        await store.append_message(
            "thread-1",
            text_message(MessageRole.ASSISTANT, "olá!"),
            parent_message_id=id1,
        )

        historico = await store.get_history("thread-1")

        assert [m.text() for m in historico] == ["oi", "olá!"]
        assert [m.role for m in historico] == [MessageRole.USER, MessageRole.ASSISTANT]

    async def test_thread_sem_mensagem_devolve_lista_vazia(self, store: SessionStore):
        await store.create_session("thread-vazia", user_id="alice")

        assert await store.get_history("thread-vazia") == []

    async def test_preserva_tool_calls_e_tool_call_id(self, store: SessionStore):
        await store.create_session("thread-1", user_id="alice")
        assistente = VMessage(
            role=MessageRole.ASSISTANT,
            content=[ContentBlock(kind="text", text="chamando tool")],
            tool_calls=[ToolCall(id="call_1", name="somar", args={"a": 1})],
        )
        id_assistente = await store.append_message("thread-1", assistente)
        await store.append_message(
            "thread-1",
            VMessage(
                role=MessageRole.TOOL,
                content=[ContentBlock(kind="text", text="2")],
                tool_call_id="call_1",
            ),
            parent_message_id=id_assistente,
        )

        historico = await store.get_history("thread-1")

        assert historico[0].tool_calls == [
            ToolCall(id="call_1", name="somar", args={"a": 1})
        ]
        assert historico[1].tool_call_id == "call_1"

    async def test_fork_via_up_to_message_id_rele_branch_antiga_sem_apagar(
        self, store: SessionStore
    ):
        await store.create_session("thread-1", user_id="alice")
        id1 = await store.append_message(
            "thread-1", text_message(MessageRole.USER, "primeira pergunta")
        )
        await store.append_message(
            "thread-1",
            text_message(MessageRole.ASSISTANT, "primeira resposta"),
            parent_message_id=id1,
        )
        # Fork: edita a mensagem original e gera uma nova branch a partir dela.
        await store.append_message(
            "thread-1",
            text_message(MessageRole.USER, "pergunta editada"),
            parent_message_id=id1,
        )

        branch_ativa = await store.get_history("thread-1")
        branch_antiga = await store.get_history("thread-1", up_to_message_id=id1)

        assert [m.text() for m in branch_ativa] == [
            "primeira pergunta",
            "pergunta editada",
        ]
        assert [m.text() for m in branch_antiga] == ["primeira pergunta"]

    async def test_get_history_with_ids_devolve_o_id_de_cada_mensagem(
        self, store: SessionStore
    ):
        await store.create_session("thread-1", user_id="alice")
        id1 = await store.append_message(
            "thread-1", text_message(MessageRole.USER, "oi")
        )
        id2 = await store.append_message(
            "thread-1",
            text_message(MessageRole.ASSISTANT, "olá!"),
            parent_message_id=id1,
        )

        pares = await store.get_history_with_ids("thread-1")

        assert [pid for pid, _msg in pares] == [id1, id2]
        assert [msg.text() for _pid, msg in pares] == ["oi", "olá!"]

    async def test_get_history_with_ids_thread_sem_mensagem_devolve_lista_vazia(
        self, store: SessionStore
    ):
        await store.create_session("thread-vazia", user_id="alice")

        assert await store.get_history_with_ids("thread-vazia") == []

    async def test_reconstroi_cadeia_longa_preservando_ordem(self, store: SessionStore):
        """`get_history_with_ids` reconstrói via uma única query recursiva
        (`WITH RECURSIVE`) em vez de uma leitura por mensagem — este teste
        cobre uma cadeia longa o bastante pra não passar por acidente com
        um `LIMIT`/`depth` mal calculado."""
        await store.create_session("thread-1", user_id="alice")
        parent_id = None
        for i in range(200):
            parent_id = await store.append_message(
                "thread-1",
                text_message(MessageRole.USER, f"mensagem {i}"),
                parent_message_id=parent_id,
            )

        historico = await store.get_history("thread-1")

        assert len(historico) == 200
        assert [m.text() for m in historico] == [f"mensagem {i}" for i in range(200)]

    async def test_ciclo_em_parent_message_id_levanta_erro_em_vez_de_recursar_sem_fim(
        self, store: SessionStore
    ):
        """A checagem antiga de ciclo era um `set` de ids visitados no loop
        Python; a reconstrução via CTE recursiva não tem como fazer o
        equivalente nativamente, então depende de um teto de profundidade
        (`_CHAIN_DEPTH_CAP`) + checagem em Python. Cria um ciclo de verdade
        via SQL cru (bypassando `append_message`, que nunca produziria um
        na prática) pra confirmar que o teto realmente barra a recursão
        em vez de travar o processo."""
        await store.create_session("thread-1", user_id="alice")
        id1 = await store.append_message(
            "thread-1", text_message(MessageRole.USER, "a")
        )
        id2 = await store.append_message(
            "thread-1",
            text_message(MessageRole.ASSISTANT, "b"),
            parent_message_id=id1,
        )
        async with store._pool.acquire() as conn:
            # id1 passa a apontar pra id2 — ciclo de 2 elos (id1 -> id2 -> id1).
            await conn.execute(
                "UPDATE messages SET parent_message_id = ? WHERE id = ?",
                (id2, id1),
            )
            await conn.execute(
                "UPDATE messages SET is_branch_head = 1 WHERE id = ?",
                (id2,),
            )
            await conn.commit()

        with pytest.raises(RuntimeError, match="ciclo detectado"):
            await store.get_history_with_ids("thread-1")


class TestGetBranchHeadId:
    async def test_thread_sem_mensagem_devolve_none(self, store: SessionStore):
        await store.create_session("thread-1", user_id="alice")

        assert await store.get_branch_head_id("thread-1") is None

    async def test_devolve_o_id_da_ultima_mensagem_apendada(self, store: SessionStore):
        await store.create_session("thread-1", user_id="alice")
        id1 = await store.append_message(
            "thread-1", text_message(MessageRole.USER, "oi")
        )
        id2 = await store.append_message(
            "thread-1",
            text_message(MessageRole.ASSISTANT, "olá"),
            parent_message_id=id1,
        )

        assert await store.get_branch_head_id("thread-1") == id2


class TestSetBranchHead:
    async def test_reaponta_a_branch_ativa_sem_apagar_mensagens(
        self, store: SessionStore
    ):
        await store.create_session("thread-1", user_id="alice")
        id1 = await store.append_message(
            "thread-1", text_message(MessageRole.USER, "pergunta")
        )
        id2 = await store.append_message(
            "thread-1",
            text_message(MessageRole.ASSISTANT, "resposta"),
            parent_message_id=id1,
        )
        await store.append_message(
            "thread-1",
            text_message(MessageRole.USER, "pergunta editada"),
            parent_message_id=id1,
        )

        await store.set_branch_head("thread-1", id2)
        historico = await store.get_history("thread-1")

        assert [m.text() for m in historico] == ["pergunta", "resposta"]

    async def test_apontar_para_mensagem_de_outra_thread_rejeita_sem_corromper_historico(
        self, store: SessionStore
    ):
        await store.create_session("thread-1", user_id="alice")
        await store.create_session("thread-2", user_id="alice")
        id_thread1 = await store.append_message(
            "thread-1", text_message(MessageRole.USER, "thread 1")
        )
        id_thread2 = await store.append_message(
            "thread-2", text_message(MessageRole.USER, "outra thread")
        )

        with pytest.raises(ValueError, match="não pertence"):
            await store.set_branch_head("thread-1", id_thread2)

        # thread-1 continua com sua própria ponta ativa — nunca fica sem head.
        assert [m.text() for m in await store.get_history("thread-1")] == ["thread 1"]
        assert await store.get_branch_head_id("thread-1") == id_thread1
        assert [m.text() for m in await store.get_history("thread-2")] == [
            "outra thread"
        ]

    async def test_apontar_para_id_inexistente_levanta_erro(self, store: SessionStore):
        await store.create_session("thread-1", user_id="alice")
        await store.append_message("thread-1", text_message(MessageRole.USER, "oi"))

        with pytest.raises(ValueError, match="não pertence"):
            await store.set_branch_head("thread-1", 999999)


class TestPendingApprovals:
    async def test_round_trip_preserva_opcoes_prioridade_e_expiracao(
        self, store: SessionStore
    ):
        await store.create_session("thread-structured", user_id="alice")
        await store.put_pending_approval(
            "thread-structured",
            interrupt_id="int-structured",
            tool_name="terminal",
            tool_call_id="call-structured",
            args={"command": "git status"},
            options=[{"label": "Executar", "value": "run"}],
            priority=80,
            expires_at="2999-01-01T00:00:00+00:00",
        )

        pending = await store.get_pending_approval("thread-structured")
        assert pending is not None
        assert pending["options"] == [{"label": "Executar", "value": "run"}]
        assert pending["priority"] == 80
        assert pending["expires_at"] == "2999-01-01T00:00:00+00:00"

    async def test_aprovacao_expirada_e_removida_ao_ser_lida(self, store: SessionStore):
        await store.create_session("thread-expired", user_id="alice")
        await store.put_pending_approval(
            "thread-expired",
            interrupt_id="int-expired",
            tool_name="terminal",
            tool_call_id="call-expired",
            args={},
            expires_at="2000-01-01T00:00:00+00:00",
        )

        assert await store.get_pending_approval("thread-expired") is None

    async def test_claim_pending_approval_e_atomico_para_duas_retomas(
        self, store: SessionStore
    ) -> None:
        """Somente uma retomada concorrente pode reivindicar o interrupt."""
        await store.create_session("thread-claim", user_id="alice")
        await store.put_pending_approval(
            "thread-claim",
            interrupt_id="intr-claim",
            tool_name="terminal",
            tool_call_id="call-claim",
            args={"command": "echo ok"},
        )

        claimed = await asyncio.gather(
            store.claim_pending_approval("thread-claim", interrupt_id="intr-claim"),
            store.claim_pending_approval("thread-claim", interrupt_id="intr-claim"),
        )

        assert sum(item is not None for item in claimed) == 1

    async def test_round_trip_put_get_clear(self, store: SessionStore):
        await store.create_session("thread-1", user_id="alice")
        await store.put_pending_approval(
            "thread-1",
            interrupt_id="int-1",
            tool_name="file_write",
            tool_call_id="call_1",
            args={"path": "a.py", "content": "x"},
            reasoning="editar arquivo",
        )

        pendente = await store.get_pending_approval("thread-1")
        assert pendente is not None
        assert pendente["tool_name"] == "file_write"
        assert pendente["args"] == {"path": "a.py", "content": "x"}
        assert pendente["reasoning"] == "editar arquivo"

        await store.clear_pending_approval("thread-1")
        assert await store.get_pending_approval("thread-1") is None

    async def test_thread_sem_pendencia_devolve_none(self, store: SessionStore):
        await store.create_session("thread-1", user_id="alice")

        assert await store.get_pending_approval("thread-1") is None

    async def test_segunda_chamada_de_put_sobrescreve_a_pendencia_anterior(
        self, store: SessionStore
    ):
        await store.create_session("thread-1", user_id="alice")
        await store.put_pending_approval(
            "thread-1",
            interrupt_id="int-1",
            tool_name="file_write",
            tool_call_id="call_1",
            args={},
        )
        await store.put_pending_approval(
            "thread-1",
            interrupt_id="int-2",
            tool_name="terminal",
            tool_call_id="call_2",
            args={"cmd": "ls"},
        )

        pendente = await store.get_pending_approval("thread-1")
        assert pendente is not None
        assert pendente["interrupt_id"] == "int-2"
        assert pendente["tool_name"] == "terminal"


class TestGetSession:
    """``get_session`` é a fonte de verdade sobre existência/posse de uma
    thread — usada pelos endpoints REST de threads pra nunca vazar uma
    thread de outro usuário."""

    async def test_devolve_metadados_da_sessao_existente(self, store: SessionStore):
        await store.create_session(
            "thread-1", user_id="alice", workspace_id="ws-1", mode="code"
        )

        session = await store.get_session("thread-1")

        assert session is not None
        assert session["thread_id"] == "thread-1"
        assert session["user_id"] == "alice"
        assert session["workspace_id"] == "ws-1"
        assert session["mode"] == "code"

    async def test_thread_inexistente_devolve_none(self, store: SessionStore):
        assert await store.get_session("nao-existe") is None

    async def test_com_user_id_correto_devolve_sessao(self, store: SessionStore):
        await store.create_session("thread-1", user_id="alice")

        assert await store.get_session("thread-1", user_id="alice") is not None

    async def test_com_user_id_de_outro_dono_devolve_none(self, store: SessionStore):
        """Erro/borda: passar `user_id` de outra pessoa não deve revelar a
        sessão — mesmo resultado (`None`) de uma thread inexistente, pra não
        vazar a quem ela pertence de verdade."""
        await store.create_session("thread-1", user_id="alice")

        assert await store.get_session("thread-1", user_id="bob") is None


class TestForeignThreadIds:
    """``foreign_thread_ids`` — usado por `ListThreads` pra excluir threads
    de outro usuário do resultado, mesmo quando outra fonte de metadados
    (`vectora_sessions`) ainda as lista."""

    async def test_lista_vazia_devolve_conjunto_vazio(self, store: SessionStore):
        assert await store.foreign_thread_ids([], "alice") == set()

    async def test_exclui_apenas_threads_de_outro_dono(self, store: SessionStore):
        await store.create_session("thread-mine", user_id="alice")
        await store.create_session("thread-theirs", user_id="bob")

        foreign = await store.foreign_thread_ids(
            ["thread-mine", "thread-theirs"], "alice"
        )

        assert foreign == {"thread-theirs"}

    async def test_thread_sem_registro_nao_e_considerada_alheia(
        self, store: SessionStore
    ):
        """Erro/borda: uma thread nunca registrada em `sessions` (legado) não
        pode ser tratada como pertencente a outro usuário — ausência de
        registro não é prova de posse alheia."""
        await store.create_session("thread-mine", user_id="alice")

        foreign = await store.foreign_thread_ids(
            ["thread-mine", "thread-sem-registro"], "alice"
        )

        assert foreign == set()


class TestListActiveUserIds:
    """``list_active_user_ids`` — usado pelo scheduler de consolidação de
    memória pra saber quais usuários tiveram atividade recente."""

    async def test_lista_apenas_usuarios_atualizados_desde_o_corte(
        self, store: SessionStore
    ):
        await store.create_session("thread-recente", user_id="alice")
        await store.create_session("thread-antiga", user_id="bob")
        async with store._pool.acquire() as conn:
            await conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE thread_id = ?",
                ("2020-01-01T00:00:00", "thread-antiga"),
            )
            await conn.commit()

        users = await store.list_active_user_ids("2025-01-01T00:00:00")

        assert users == ["alice"]

    async def test_sem_sessao_nenhuma_devolve_lista_vazia(self, store: SessionStore):
        """Erro/borda: banco vazio nunca lança, devolve lista vazia."""
        assert await store.list_active_user_ids("2020-01-01T00:00:00") == []

    async def test_dois_usuarios_ativos_aparecem_sem_duplicata(
        self, store: SessionStore
    ):
        await store.create_session("thread-a", user_id="alice")
        await store.create_session("thread-a2", user_id="alice")
        await store.create_session("thread-b", user_id="bob")

        users = await store.list_active_user_ids("2020-01-01T00:00:00")

        assert sorted(users) == ["alice", "bob"]
