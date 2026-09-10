"""``should_require_approval``/``ApprovalGate`` — HITL nativo. Migração da
política canônica de `backend/services/middleware.py` pra função pura,
mais a persistência síncrona da aprovação pendente (sobrevivência a
restart, sobre o SessionStore).
"""

from __future__ import annotations

import asyncio

import pytest

from backend.engine.hitl import REQUIRE_APPROVAL, ApprovalGate, should_require_approval
from backend.persistence.native.session_store import SessionStore
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.tools.context import ToolContext
from backend.vtypes.message import ContentBlock, MessageRole, VMessage, text_message


@pytest.fixture
async def session_store(tmp_path):
    pool = AsyncConnectionPool(str(tmp_path / "hitl.db"), min_size=1, max_size=2)
    await pool.open()
    store = SessionStore(pool)
    await store.setup()
    await store.create_session("thread-1", user_id="alice")
    try:
        yield store
    finally:
        await pool.close()


def _ctx(**kwargs) -> ToolContext:
    return ToolContext(user_id="alice", thread_id="thread-1", **kwargs)


def test_write_terminal_requires_approval() -> None:
    assert "write_terminal" in REQUIRE_APPROVAL
    assert should_require_approval("write_terminal", _ctx(), {}, []) is True
    assert (
        should_require_approval("write_terminal", _ctx(permission_mode="auto"), {}, [])
        is False
    )


class TestShouldRequireApproval:
    def test_tool_fora_da_lista_nunca_interrompe(self):
        assert (
            should_require_approval("web_search", _ctx(permission_mode="ask"), {}, [])
            is False
        )

    def test_modo_ask_interrompe_tool_destrutiva(self):
        assert (
            should_require_approval("file_write", _ctx(permission_mode="ask"), {}, [])
            is True
        )

    def test_modo_desconhecido_cai_no_comportamento_mais_restritivo_de_ask(self):
        ctx = _ctx(permission_mode="modo-invalido")
        assert should_require_approval("file_write", ctx, {}, []) is True

    def test_modo_accept_edits_auto_aprova_edicao_mas_nao_terminal(self):
        ctx = _ctx(permission_mode="accept_edits")
        assert should_require_approval("file_write", ctx, {}, []) is False
        assert should_require_approval("terminal", ctx, {}, []) is True

    def test_modos_auto_e_bypass_nunca_interrompem(self):
        for modo in ("auto", "bypass"):
            ctx = _ctx(permission_mode=modo)
            assert should_require_approval("file_write", ctx, {}, []) is False

    def test_computer_use_sempre_interrompe_mesmo_em_bypass(self):
        ctx = _ctx(permission_mode="bypass")
        assert should_require_approval("computer_use", ctx, {}, []) is True

    def test_modo_plan_interrompe_so_a_primeira_tool_destrutiva_do_turno(self):
        ctx = _ctx(permission_mode="plan")
        historico_vazio: list[VMessage] = []
        assert should_require_approval("file_write", ctx, {}, historico_vazio) is True

        historico_ja_aprovado = [
            text_message(MessageRole.USER, "faça algo"),
            VMessage(
                role=MessageRole.ASSISTANT,
                content=[ContentBlock(kind="text", text="ok")],
            ),
            VMessage(
                role=MessageRole.TOOL,
                content=[ContentBlock(kind="text", text="feito")],
                name="file_write",
            ),
        ]
        assert (
            should_require_approval("file_write", ctx, {}, historico_ja_aprovado)
            is False
        )

    def test_modo_plan_reinicia_a_cada_nova_mensagem_do_usuario(self):
        ctx = _ctx(permission_mode="plan")
        historico = [
            text_message(MessageRole.USER, "primeiro pedido"),
            VMessage(
                role=MessageRole.TOOL,
                content=[ContentBlock(kind="text", text="feito")],
                name="file_write",
            ),
            text_message(MessageRole.USER, "segundo pedido"),
        ]
        # Depois do 2º USER, nenhuma tool rodou ainda — volta a interromper.
        assert should_require_approval("file_write", ctx, {}, historico) is True

    def test_kanban_update_status_da_propria_task_nunca_interrompe(self):
        ctx = _ctx(permission_mode="ask", background_task_id="task-1")
        args = {"task_id": "task-1"}
        assert should_require_approval("kanban_update_status", ctx, args, []) is False

    def test_kanban_update_status_de_outra_task_interrompe_normalmente(self):
        ctx = _ctx(permission_mode="ask", background_task_id="task-1")
        args = {"task_id": "task-2"}
        assert should_require_approval("kanban_update_status", ctx, args, []) is True

    def test_workspace_jailed_dispensa_aprovacao_de_terminal_e_file_write(
        self, monkeypatch
    ):
        monkeypatch.setattr(
            "backend.engine.hitl._workspace_is_jailed", lambda _wsid: True
        )
        ctx = _ctx(permission_mode="ask", workspace_id="ws-1")
        assert should_require_approval("terminal", ctx, {}, []) is False
        assert should_require_approval("file_write", ctx, {}, []) is False
        # git/install continuam pedindo aprovação mesmo jailed.
        assert should_require_approval("install_learned_skill", ctx, {}, []) is True

    def test_workspace_nao_jailed_continua_pedindo_aprovacao(self, monkeypatch):
        monkeypatch.setattr(
            "backend.engine.hitl._workspace_is_jailed", lambda _wsid: False
        )
        ctx = _ctx(permission_mode="ask", workspace_id="ws-1")
        assert should_require_approval("terminal", ctx, {}, []) is True


class TestApprovalGate:
    async def test_midia_persiste_apenas_metadados_e_mantem_payload_efemero(
        self, session_store
    ):
        gate = ApprovalGate(session_store)
        args = {"prompt": "segredo", "size": "1024x1024"}
        metadata = {
            "operation": "generate_image",
            "provider": "openai",
            "model": "gpt-image-1",
            "estimate_version": "v1",
            "billable_unit": "quota_units",
            "currency": "quota_units",
            "estimated_units": 1,
            "idempotency_key": "idem-1",
        }

        await gate.request_approval(
            "thread-1",
            interrupt_id="int-1",
            tool_name="generate_image",
            tool_call_id="call_1",
            args=args,
            approval_metadata=metadata,
        )

        pending = await session_store.get_pending_approval("thread-1")
        assert pending is not None
        assert pending["args"] == metadata
        assert "prompt" not in pending["args"]
        assert gate.ephemeral_args("int-1") == args

    async def test_request_approval_persiste_no_session_store(self, session_store):
        gate = ApprovalGate(session_store)

        await gate.request_approval(
            "thread-1",
            interrupt_id="int-1",
            tool_name="file_write",
            tool_call_id="call_1",
            args={"path": "a.py"},
            reasoning="criar arquivo",
        )

        pendente = await session_store.get_pending_approval("thread-1")
        assert pendente is not None
        assert pendente["tool_name"] == "file_write"
        assert pendente["args"] == {"path": "a.py"}

    async def test_resolve_limpa_a_pendencia_e_libera_o_fast_path(self, session_store):
        gate = ApprovalGate(session_store)
        await gate.request_approval(
            "thread-1",
            interrupt_id="int-1",
            tool_name="file_write",
            tool_call_id="call_1",
            args={},
        )

        await gate.resolve("thread-1")

        assert await session_store.get_pending_approval("thread-1") is None

    async def test_wait_for_resume_retorna_true_quando_resolve_e_chamado(
        self, session_store
    ):
        gate = ApprovalGate(session_store)
        await gate.request_approval(
            "thread-1",
            interrupt_id="int-1",
            tool_name="file_write",
            tool_call_id="call_1",
            args={},
        )

        async def _resolver_logo():
            await asyncio.sleep(0.01)
            await gate.resolve("thread-1")

        resolver_task = asyncio.create_task(_resolver_logo())
        liberado = await gate.wait_for_resume("thread-1", timeout_s=1.0)
        await resolver_task

        assert liberado is True

    async def test_wait_for_resume_sem_pedido_previo_devolve_false_sem_travar(
        self, session_store
    ):
        gate = ApprovalGate(session_store)

        liberado = await gate.wait_for_resume("thread-sem-pedido", timeout_s=0.05)

        assert liberado is False

    async def test_wait_for_resume_expira_no_timeout_se_ninguem_resolver(
        self, session_store
    ):
        gate = ApprovalGate(session_store)
        await gate.request_approval(
            "thread-1",
            interrupt_id="int-1",
            tool_name="file_write",
            tool_call_id="call_1",
            args={},
        )

        liberado = await gate.wait_for_resume("thread-1", timeout_s=0.05)

        assert liberado is False
