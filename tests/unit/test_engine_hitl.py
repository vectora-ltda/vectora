"""``should_require_approval``/``ApprovalGate`` — HITL nativo. Migração da
política canônica de `backend/services/middleware.py` pra função pura,
mais a persistência síncrona da aprovação pendente (sobrevivência a
restart, sobre o SessionStore).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal

import pytest

from backend.engine.hitl import (
    REQUIRE_APPROVAL,
    ApprovalGate,
    _decision_args_summary,
    call_requires_separate_approval,
    should_require_approval,
)
from backend.persistence.native.session_store import SessionStore
from backend.storage.sqlite.pool import AsyncConnectionPool
from backend.tools.context import ToolContext
from backend.vtypes.message import ContentBlock, MessageRole, VMessage, text_message


@pytest.fixture
async def session_store(tmp_path):
    """Executa a operação session store."""
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
    """Executa a operação ctx."""
    return ToolContext(user_id="alice", thread_id="thread-1", **kwargs)


def test_write_terminal_requires_approval() -> None:
    """Verifica o comportamento de write terminal requires approval."""
    assert "write_terminal" in REQUIRE_APPROVAL
    assert should_require_approval("write_terminal", _ctx(), {}, []) is True
    assert (
        should_require_approval("write_terminal", _ctx(permission_mode="auto"), {}, [])
        is False
    )


def test_decision_args_summary_includes_structured_values_and_redacts_secrets() -> None:
    summary = _decision_args_summary(
        {
            "command": "git push origin feature",
            "api_key": "secret-value",
            "authorization": "Bearer abcdefghijklmnopqrstuvwxyz012345",
        }
    )

    assert '"command":"git push origin feature"' in summary
    assert '"api_key":"[REDACTED]"' in summary
    assert '"authorization":"[REDACTED]"' in summary
    assert "secret-value" not in summary
    assert "abcdefghijklmnopqrstuvwxyz012345" not in summary


def test_decision_args_summary_is_bounded() -> None:
    summary = _decision_args_summary({"prompt": "x" * 5000})

    assert len(summary) == 4000
    assert summary.endswith("...")


class TestShouldRequireApproval:
    def test_tool_fora_da_lista_nunca_interrompe(self):
        """Verifica o comportamento de tool fora da lista nunca interrompe."""
        assert (
            should_require_approval("web_search", _ctx(permission_mode="ask"), {}, [])
            is False
        )

    def test_modo_ask_interrompe_tool_destrutiva(self):
        """Verifica o comportamento de modo ask interrompe tool destrutiva."""
        assert (
            should_require_approval("file_write", _ctx(permission_mode="ask"), {}, [])
            is True
        )

    def test_toda_tool_marcada_como_destrutiva_no_registry_pede_aprovacao(self) -> None:
        """O metadata do registry é a segunda metade do contrato HITL.

        O teste importa o catálogo nativo inteiro para impedir que uma nova
        tool destrutiva seja adicionada sem entrar automaticamente na política
        de aprovação.
        """
        import backend.nodes.tools  # registra as tools nativas
        from backend.tools.registry import TOOL_REGISTRY

        destrutivas = [spec for spec in TOOL_REGISTRY.all() if spec.extras.destructive]
        assert destrutivas
        assert all(
            should_require_approval(spec.name, _ctx(permission_mode="ask"), {}, [])
            for spec in destrutivas
        )

    def test_modo_desconhecido_cai_no_comportamento_mais_restritivo_de_ask(self):
        """Verifica o comportamento de modo desconhecido cai no comportamento mais restritivo de ask."""
        ctx = _ctx(permission_mode="modo-invalido")
        assert should_require_approval("file_write", ctx, {}, []) is True

    def test_modo_accept_edits_auto_aprova_edicao_mas_nao_terminal(self):
        """Verifica o comportamento de modo accept edits auto aprova edicao mas nao terminal."""
        ctx = _ctx(permission_mode="accept_edits")
        assert should_require_approval("file_write", ctx, {}, []) is False
        assert should_require_approval("terminal", ctx, {}, []) is True

    def test_modos_auto_e_bypass_nunca_interrompem(self):
        """Verifica o comportamento de modos auto e bypass nunca interrompem."""
        for modo in ("auto", "bypass"):
            ctx = _ctx(permission_mode=modo)
            assert should_require_approval("file_write", ctx, {}, []) is False

    def test_midia_falha_fechada_com_limiar_ausente_ou_invalido(self):
        """Verifica o comportamento de midia falha fechada com limiar ausente ou invalido."""
        for threshold in (None, True, "1"):
            ctx = _ctx(permission_mode="auto", model="openai:gpt-image-1")
            if threshold is not None:
                ctx._extra["media_approval_threshold"] = threshold
            assert should_require_approval("generate_image", ctx, {}, []) is True

    def test_computer_use_sempre_interrompe_mesmo_em_bypass(self):
        """Verifica o comportamento de computer use sempre interrompe mesmo em bypass."""
        ctx = _ctx(permission_mode="bypass")
        assert should_require_approval("computer_use", ctx, {}, []) is True

    def test_modo_plan_interrompe_so_a_primeira_tool_destrutiva_do_turno(self):
        """Verifica o comportamento de modo plan interrompe so a primeira tool destrutiva do turno."""
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
                tool_call_id="approved-write",
            ),
        ]
        assert (
            should_require_approval("file_write", ctx, {}, historico_ja_aprovado)
            is True
        )
        ctx._extra["plan_approved_tool_call_ids"] = ["approved-write"]
        assert (
            should_require_approval("file_write", ctx, {}, historico_ja_aprovado)
            is False
        )

    def test_modo_plan_reinicia_a_cada_nova_mensagem_do_usuario(self):
        """Verifica o comportamento de modo plan reinicia a cada nova mensagem do usuario."""
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
        """Verifica o comportamento de kanban update status da propria task nunca interrompe."""
        ctx = _ctx(permission_mode="ask", background_task_id="task-1")
        args = {"task_id": "task-1"}
        assert should_require_approval("kanban_update_status", ctx, args, []) is False

    def test_kanban_update_status_de_outra_task_interrompe_normalmente(self):
        """Verifica o comportamento de kanban update status de outra task interrompe normalmente."""
        ctx = _ctx(permission_mode="ask", background_task_id="task-1")
        args = {"task_id": "task-2"}
        assert should_require_approval("kanban_update_status", ctx, args, []) is True

    def test_workspace_jailed_dispensa_aprovacao_de_terminal_e_file_write(
        self, monkeypatch
    ):
        """Verifica o comportamento de workspace jailed dispensa aprovacao de terminal e file write."""
        monkeypatch.setattr(
            "backend.engine.hitl._workspace_is_jailed", lambda _wsid: True
        )
        monkeypatch.setattr(
            "backend.engine.hitl._jail_controls_network", lambda _wsid: True
        )
        ctx = _ctx(permission_mode="ask", workspace_id="ws-1")
        assert should_require_approval("terminal", ctx, {}, []) is False
        assert should_require_approval("file_write", ctx, {}, []) is False
        # git/install continuam pedindo aprovação mesmo jailed.
        assert should_require_approval("install_learned_skill", ctx, {}, []) is True

    @pytest.mark.parametrize("tool", ["terminal", "file_write", "file_edit"])
    @pytest.mark.parametrize("jailed", [True, False])
    def test_plan_first_action_respects_sandbox_exemption(
        self, monkeypatch: pytest.MonkeyPatch, tool: str, jailed: bool
    ) -> None:
        """A primeira ação sem histórico só é dispensada quando há sandbox."""
        monkeypatch.setattr(
            "backend.engine.hitl._workspace_is_jailed", lambda _wsid: jailed
        )
        monkeypatch.setattr(
            "backend.engine.hitl._jail_controls_network", lambda _wsid: True
        )
        ctx = _ctx(permission_mode="plan", workspace_id="ws-1")
        assert should_require_approval(tool, ctx, {}, []) is not jailed
        assert should_require_approval("install_learned_skill", ctx, {}, []) is True

    def test_terminal_em_jail_com_rede_aberta_continua_pedindo_aprovacao(
        self, monkeypatch
    ):
        """Jail vivo, mas com a rede do host aberta: o terminal executa código
        arbitrário e poderia exfiltrar, então a pausa não é dispensada."""
        monkeypatch.setattr(
            "backend.engine.hitl._workspace_is_jailed", lambda _wsid: True
        )
        monkeypatch.setattr(
            "backend.engine.hitl._jail_controls_network", lambda _wsid: False
        )
        ctx = _ctx(permission_mode="ask", workspace_id="ws-1")

        assert should_require_approval("terminal", ctx, {}, []) is True
        # Escrever arquivos não depende da rede: segue dispensado.
        assert should_require_approval("file_write", ctx, {}, []) is False
        assert should_require_approval("file_edit", ctx, {}, []) is False

    def test_workspace_nao_jailed_continua_pedindo_aprovacao(self, monkeypatch):
        """Verifica o comportamento de workspace nao jailed continua pedindo aprovacao."""
        monkeypatch.setattr(
            "backend.engine.hitl._workspace_is_jailed", lambda _wsid: False
        )
        ctx = _ctx(permission_mode="ask", workspace_id="ws-1")
        assert should_require_approval("terminal", ctx, {}, []) is True


class TestApprovalGate:
    async def test_midia_persiste_apenas_metadados_e_mantem_payload_efemero(
        self, session_store: SessionStore
    ) -> None:
        """Verifica o comportamento de midia persiste apenas metadados e mantem payload efemero."""
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

    async def test_request_approval_persiste_no_session_store(
        self, session_store: SessionStore
    ) -> None:
        """Verifica o comportamento de request approval persiste no session store."""
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
        """Verifica o comportamento de resolve limpa a pendencia e libera o fast path."""
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

    async def test_resolve_limpa_args_efemeros_apos_claim(self, session_store) -> None:
        """A limpeza usa o interrupt_id mesmo depois de a pendência ser consumida."""
        gate = ApprovalGate(session_store)
        await gate.request_approval(
            "thread-1",
            interrupt_id="int-media",
            tool_name="generate_image",
            tool_call_id="call-media",
            args={"prompt": "segredo"},
        )

        claimed = await session_store.claim_pending_approval(
            "thread-1", interrupt_id="int-media"
        )
        assert claimed is not None
        assert gate.ephemeral_args("int-media") == {"prompt": "segredo"}

        await gate.resolve("thread-1", interrupt_id="int-media")

        assert gate.ephemeral_args("int-media") is None

    async def test_wait_for_resume_retorna_true_quando_resolve_e_chamado(
        self, session_store
    ):
        """Verifica o comportamento de wait for resume retorna true quando resolve e chamado."""
        gate = ApprovalGate(session_store)
        await gate.request_approval(
            "thread-1",
            interrupt_id="int-1",
            tool_name="file_write",
            tool_call_id="call_1",
            args={},
        )

        async def _resolver_logo():
            """Executa a operação resolver logo."""
            await asyncio.sleep(0.01)
            await gate.resolve("thread-1")

        resolver_task = asyncio.create_task(_resolver_logo())
        liberado = await gate.wait_for_resume("thread-1", timeout_s=1.0)
        await resolver_task

        assert liberado is True

    async def test_wait_for_resume_sem_pedido_previo_devolve_false_sem_travar(
        self, session_store
    ):
        """Verifica o comportamento de wait for resume sem pedido previo devolve false sem travar."""
        gate = ApprovalGate(session_store)

        liberado = await gate.wait_for_resume("thread-sem-pedido", timeout_s=0.05)

        assert liberado is False

    async def test_wait_for_resume_expira_no_timeout_se_ninguem_resolver(
        self, session_store
    ):
        """Verifica o comportamento de wait for resume expira no timeout se ninguem resolver."""
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


def test_call_requires_separate_approval():
    # Tools destrutivas padrão exigem aprovação
    """Verifica o comportamento de call requires separate approval."""
    assert call_requires_separate_approval("file_write") is True
    assert call_requires_separate_approval("git_push") is True

    # git_push com force=True
    assert call_requires_separate_approval("git_push", {"force": True}) is True

    # http_request: GET não exige, métodos mutáveis exigem
    assert call_requires_separate_approval("http_request", {"method": "GET"}) is False
    assert call_requires_separate_approval("http_request", {}) is False
    assert call_requires_separate_approval("http_request", {"method": "POST"}) is True
    assert call_requires_separate_approval("http_request", {"method": "PUT"}) is True
    assert call_requires_separate_approval("http_request", {"method": "PATCH"}) is True
    assert call_requires_separate_approval("http_request", {"method": "DELETE"}) is True

    # Tool de leitura segura
    assert call_requires_separate_approval("file_read", {"path": "a.txt"}) is False


@pytest.mark.parametrize("tool", ["terminal", "file_write", "file_edit"])
@pytest.mark.parametrize(
    ("isolated", "alive", "expected"),
    [(False, True, True), (True, True, False), (True, False, True)],
)
def test_plan_sandbox_exemption_requires_a_live_isolated_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
    isolated: bool,
    alive: bool,
    expected: bool,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from backend.sandbox.workspace_jail import JailedWorker, jail_manager
    from backend.workspace.workspace import workspace_registry

    (tmp_path / "vectora.toml").write_text(
        "[sandbox]\nenabled=true\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        workspace_registry, "get", lambda _wid: SimpleNamespace(cwd=str(tmp_path))
    )
    from backend.sandbox.policy import SandboxPolicy

    worker = JailedWorker(
        workspace_id="ws-soft",
        proc=MagicMock(returncode=None if alive else 1),
        isolation_enforced=isolated,
        policy=SandboxPolicy(enabled=True),
    )
    monkeypatch.setattr(jail_manager, "_workers", {"ws-soft": worker})
    monkeypatch.setattr(jail_manager, "_isolation_required", set())
    ctx = _ctx(permission_mode="plan", workspace_id="ws-soft")
    assert should_require_approval(tool, ctx, {}, []) is expected
    assert should_require_approval("git_push", ctx, {}, []) is True


@pytest.mark.parametrize(
    ("network", "terminal_pauses"),
    [("filtered", False), ("off", False), ("open", True)],
)
def test_terminal_so_dispensa_aprovacao_com_a_rede_do_jail_sob_controle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    network: Literal["off", "filtered", "open"],
    terminal_pauses: bool,
) -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from backend.sandbox.policy import SandboxPolicy
    from backend.sandbox.workspace_jail import JailedWorker, jail_manager
    from backend.workspace.workspace import workspace_registry

    (tmp_path / "vectora.toml").write_text(
        "[sandbox]\nenabled=true\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        workspace_registry, "get", lambda _wid: SimpleNamespace(cwd=str(tmp_path))
    )
    worker = JailedWorker(
        workspace_id="ws-net",
        proc=MagicMock(returncode=None),
        isolation_enforced=True,
        policy=SandboxPolicy(enabled=True, network=network),
    )
    monkeypatch.setattr(jail_manager, "_workers", {"ws-net": worker})
    monkeypatch.setattr(jail_manager, "_isolation_required", set())
    ctx = _ctx(permission_mode="plan", workspace_id="ws-net")

    assert should_require_approval("terminal", ctx, {}, []) is terminal_pauses
    assert should_require_approval("file_write", ctx, {}, []) is False


def test_worker_startup_does_not_change_policy_snapshot_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from backend.sandbox.workspace_jail import JailedWorker, jail_manager
    from backend.services.policy_compiler import compile_policy_snapshot
    from backend.workspace.workspace import workspace_registry

    (tmp_path / "vectora.toml").write_text(
        "[sandbox]\nenabled=true\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        workspace_registry, "get", lambda _wid: SimpleNamespace(cwd=str(tmp_path))
    )
    monkeypatch.setattr(jail_manager, "_workers", {})
    monkeypatch.setattr(jail_manager, "_isolation_required", set())
    ctx = _ctx(permission_mode="plan", workspace_id="ws-stable")
    before = compile_policy_snapshot(ctx)
    assert should_require_approval("file_write", ctx, {}, []) is True
    jail_manager._workers[ctx.workspace_id] = JailedWorker(
        ctx.workspace_id, MagicMock(returncode=None), isolation_enforced=True
    )
    after = compile_policy_snapshot(ctx)
    assert before.version == after.version
    assert should_require_approval("file_write", ctx, {}, []) is False
