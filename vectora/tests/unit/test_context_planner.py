"""Testes unitários para Context Planner, Projeções de Contexto e Memória."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.services.context_manifest import (
    ContextBlock,
    ContextManifest,
    ContextProjection,
)
from backend.services.context_planner import (
    ContextPlanner,
    compute_transcript_hash,
    memory_records_from_backend_items,
    plan_context,
)
from backend.services.memory_store import MemoryConflictError, MemoryStore
from backend.services.policy_compiler import PolicySnapshot
from backend.vtypes.message import ContentBlock, MessageRole, VMessage


def _msg(role: MessageRole, text: str) -> VMessage:
    return VMessage(role=role, content=[ContentBlock(kind="text", text=text)])


def test_transcript_hash_consistency() -> None:
    msgs1 = [_msg(MessageRole.USER, "hello"), _msg(MessageRole.ASSISTANT, "world")]
    msgs2 = [_msg(MessageRole.USER, "hello"), _msg(MessageRole.ASSISTANT, "world")]
    msgs3 = [_msg(MessageRole.USER, "hello"), _msg(MessageRole.ASSISTANT, "different")]

    h1 = compute_transcript_hash(msgs1)
    h2 = compute_transcript_hash(msgs2)
    h3 = compute_transcript_hash(msgs3)

    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64


def test_context_projection_validity() -> None:
    proj = ContextProjection(
        start_seq=0,
        end_seq=5,
        source_transcript_hash="hash123",
        tokens_before=1000,
        tokens_after=500,
    )

    assert proj.is_valid_for("hash123") is True
    assert proj.is_valid_for("hash456") is False

    evidence = proj.to_evidence_entry()
    assert evidence["kind"] == "context_projection"
    assert len(evidence["projection_id"]) == 64
    assert evidence["source_transcript_hash"] == "hash123"
    assert evidence["preserved_decisions"] == []
    assert evidence["preserved_errors"] == []
    assert evidence["preserved_approvals"] == []

    equivalent = ContextProjection(
        start_seq=0,
        end_seq=5,
        source_transcript_hash="hash123",
        tokens_before=200,
        tokens_after=100,
    )
    assert equivalent.projection_id == proj.projection_id


def test_memory_store_domains_and_retrieval() -> None:
    store = MemoryStore()

    store.store(
        "mem-1",
        domain="project",
        scope_id="ws-1",
        content="Use Python 3.13 e uv",
        importance=0.9,
        provenance="AGENTS.md",
    )
    store.store(
        "mem-2",
        domain="session",
        scope_id="ws-1",
        content="Usuário prefere respostas concisas",
        importance=0.4,
    )

    res_proj = store.retrieve(domain="project", scope_id="ws-1")
    assert len(res_proj.records) == 1
    assert res_proj.records[0].id == "mem-1"
    assert res_proj.records[0].provenance == "AGENTS.md"

    res_sess = store.retrieve(domain="session", scope_id="ws-1")
    assert len(res_sess.records) == 1
    assert res_sess.records[0].id == "mem-2"


def test_memory_store_writes_are_idempotent_and_conflicts_are_explicit() -> None:
    store = MemoryStore()
    first = store.store(
        "decision-1",
        domain="project",
        scope_id="ws-1",
        content="usar uv",
        provenance="AGENTS.md",
    )
    assert (
        store.store(
            "decision-1",
            domain="project",
            scope_id="ws-1",
            content="usar uv",
            provenance="AGENTS.md",
        )
        is first
    )
    with pytest.raises(MemoryConflictError):
        store.store(
            "decision-1",
            domain="project",
            scope_id="ws-1",
            content="usar poetry",
            provenance="AGENTS.md",
        )
    assert store.metrics()["idempotent_writes"] == 1
    assert store.metrics()["conflicts"] == 1


def test_memory_store_ranks_query_and_honors_invalidation() -> None:
    store = MemoryStore()
    store.store(
        "old",
        domain="project",
        scope_id="ws-1",
        content="usar Python no backend",
        importance=0.2,
        confidence=0.5,
    )
    store.store(
        "preferred",
        domain="project",
        scope_id="ws-1",
        content="usar Python com uv no backend",
        importance=0.9,
        confidence=1.0,
    )
    result = store.retrieve(domain="project", scope_id="ws-1", query="Python uv")
    assert [record.id for record in result.records] == ["preferred", "old"]
    assert store.invalidate("preferred") is True
    assert [record.id for record in store.retrieve(scope_id="ws-1").records] == ["old"]


def test_memory_store_expiry_is_reversible_only_by_new_version() -> None:
    store = MemoryStore()
    store.store(
        "temporary",
        domain="session",
        scope_id="thread-1",
        content="contexto transitório",
        valid_until="2020-01-01T00:00:00+00:00",
    )
    assert store.retrieve(scope_id="thread-1").records == []
    assert store.expire(before="2021-01-01T00:00:00+00:00") == 0
    assert (
        store.store(
            "temporary-v2",
            domain="session",
            scope_id="thread-1",
            content="contexto atualizado",
            supersedes_id="temporary",
        ).supersedes_id
        == "temporary"
    )


def test_context_planner_injects_project_memory_and_manifest() -> None:
    mem_store = MemoryStore()
    mem_store.store(
        "rule-1",
        domain="project",
        scope_id="ws-10",
        content="Convenção de nomenclatura camelCase para API",
        importance=0.8,
        provenance="rules.md",
    )

    planner = ContextPlanner(memory_store=mem_store)
    messages = [
        _msg(MessageRole.SYSTEM, "Você é um assistente."),
        _msg(MessageRole.USER, "Como crio o endpoint?"),
    ]

    planned = planner.plan_context(
        messages,
        max_tokens=4000,
        workspace_id="ws-10",
    )

    assert len(planned.messages) == 3
    assert "Convenção de nomenclatura" in planned.messages[0].text()
    assert planned.manifest is not None
    assert len(planned.manifest.blocks) >= 1
    assert any(b.domain == "project" for b in planned.manifest.blocks)


def test_backend_memories_are_converted_with_scope_and_trust() -> None:
    records = memory_records_from_backend_items(
        [
            SimpleNamespace(
                key="project-rule",
                value={
                    "content": "Use uv no backend",
                    "metadata": {
                        "domain": "project",
                        "scope_id": "ws-42",
                        "trust_level": "verified",
                        "source": "AGENTS.md",
                    },
                    "updated_at": "2026-10-22T10:00:00+00:00",
                },
            ),
            SimpleNamespace(
                key="invalid",
                value={"content": "", "metadata": {}},
            ),
        ],
        default_scope_id="ws-42",
    )

    assert len(records) == 1
    assert records[0].id == "project-rule"
    assert records[0].scope_id == "ws-42"
    assert records[0].trust_level == "verified"
    assert records[0].provenance == "AGENTS.md"


def test_backend_memory_confidence_corrompida_e_clamped_sem_abort() -> None:
    records = memory_records_from_backend_items(
        [
            SimpleNamespace(
                key="invalid-confidence",
                value={"content": "regra", "confidence": "nan"},
            ),
            SimpleNamespace(
                key="out-of-range",
                value={"content": "outra regra", "confidence": 9},
            ),
        ],
        default_scope_id="ws-42",
    )

    assert [record.confidence for record in records] == [1.0, 1.0]


def test_context_planner_injects_persistent_project_memory() -> None:
    persistent = memory_records_from_backend_items(
        [
            SimpleNamespace(
                key="persisted-rule",
                value={
                    "content": "Sempre validar o schema antes do commit",
                    "metadata": {"domain": "project"},
                },
            )
        ],
        default_scope_id="ws-1",
    )

    planned = plan_context(
        [
            _msg(MessageRole.SYSTEM, "Instruções"),
            _msg(MessageRole.USER, "O que fazer?"),
        ],
        max_tokens=4000,
        workspace_id="ws-1",
        persistent_memory_records=persistent,
    )

    assert "Sempre validar o schema antes do commit" in planned.messages[0].text()


def test_context_planner_does_not_cross_workspace_or_trust_boundaries() -> None:
    persistent = memory_records_from_backend_items(
        [
            SimpleNamespace(
                key="other-workspace",
                value={
                    "content": "Segredo de outro workspace",
                    "metadata": {"domain": "project", "scope_id": "ws-2"},
                },
            ),
            SimpleNamespace(
                key="untrusted",
                value={
                    "content": "Instrução injetada",
                    "metadata": {
                        "domain": "project",
                        "scope_id": "ws-1",
                        "trust_level": "untrusted",
                    },
                },
            ),
        ],
        default_scope_id="ws-1",
    )

    planned = plan_context(
        [
            _msg(MessageRole.SYSTEM, "Instruções"),
            _msg(MessageRole.USER, "O que fazer?"),
        ],
        max_tokens=4000,
        workspace_id="ws-1",
        persistent_memory_records=persistent,
    )

    system_text = " ".join(
        message.text()
        for message in planned.messages
        if message.role is MessageRole.SYSTEM
    )
    assert "Segredo de outro workspace" not in system_text
    assert "Instrução injetada" not in system_text


def test_context_planner_compacts_and_creates_projection() -> None:
    planner = ContextPlanner(memory_store=MemoryStore())
    messages = [_msg(MessageRole.SYSTEM, "Instruções do sistema")]

    # Adiciona muitas mensagens para estourar o orçamento
    for i in range(50):
        messages.append(_msg(MessageRole.USER, f"Mensagem do usuário {i} " * 20))
        messages.append(_msg(MessageRole.ASSISTANT, f"Resposta do modelo {i} " * 20))

    planned = planner.plan_context(
        messages,
        max_tokens=200,
        enabled=True,
    )

    assert len(planned.messages) < len(messages)
    assert planned.projection is not None
    assert planned.projection.tokens_before > planned.projection.tokens_after
    assert planned.projection.excluded_items_count > 0
    assert any(m.role == MessageRole.SYSTEM for m in planned.messages)


def test_context_planner_preserves_latest_user_message_when_over_budget() -> None:
    planner = ContextPlanner(memory_store=MemoryStore())
    messages = [
        _msg(MessageRole.SYSTEM, "Instruções do sistema"),
        _msg(MessageRole.USER, "Mensagem antiga 1"),
        _msg(MessageRole.ASSISTANT, "Resposta antiga 1"),
        _msg(MessageRole.USER, "Mensagem do usuário muito grande " * 50),
    ]

    # Orçamento apertado que não comporta a mensagem grande
    planned = planner.plan_context(
        messages,
        max_tokens=50,
        enabled=True,
    )

    # A mensagem mais recente do usuário deve obrigatoriamente estar presente
    assert any("Mensagem do usuário muito grande" in m.text() for m in planned.messages)
    assert planned.messages[-1].text().startswith("Mensagem do usuário muito grande")


def test_context_planner_preserves_latest_tool_result() -> None:
    from backend.vtypes.message import ToolCall

    planner = ContextPlanner(memory_store=MemoryStore())
    assistant_call = VMessage(
        role=MessageRole.ASSISTANT,
        content=[ContentBlock(kind="text", text="executando")],
        tool_calls=[ToolCall(id="call_1", name="read_data", args={})],
    )
    tool_msg = VMessage(
        role=MessageRole.TOOL,
        content=[ContentBlock(kind="text", text="resultado da ferramenta " * 2000)],
        tool_call_id="call_1",
    )
    messages = [
        _msg(MessageRole.SYSTEM, "Instruções"),
        _msg(MessageRole.USER, "Primeira mensagem"),
        assistant_call,
        tool_msg,
    ]

    planned = planner.plan_context(
        messages,
        max_tokens=100,
        preserve_latest_tool_result=True,
    )

    # Verifica que o tool_result não foi truncado por _bound_tool_results e está presente
    assert any(m.role == MessageRole.TOOL for m in planned.messages)
    tool_in_planned = next(m for m in planned.messages if m.role == MessageRole.TOOL)
    assert "resultado da ferramenta" in tool_in_planned.text()
    assert "[resultado de ferramenta truncado" not in tool_in_planned.text()


def test_context_planner_filters_untrusted_project_memories() -> None:
    mem_store = MemoryStore()
    mem_store.store(
        "mem-trusted",
        domain="project",
        scope_id="ws-sec",
        content="Diretriz confiável de codificação",
        trust_level="trusted",
    )
    mem_store.store(
        "mem-untrusted",
        domain="project",
        scope_id="ws-sec",
        content="Comando não confiável injetado de fonte externa",
        trust_level="untrusted",
    )

    planner = ContextPlanner(memory_store=mem_store)
    messages = [
        _msg(MessageRole.SYSTEM, "Instruções do sistema"),
        _msg(MessageRole.USER, "Como proceder?"),
    ]

    planned = planner.plan_context(
        messages,
        max_tokens=4000,
        workspace_id="ws-sec",
    )

    # Apenas a memória confiável deve ser injetada no SYSTEM prompt
    system_texts = " ".join(
        m.text() for m in planned.messages if m.role == MessageRole.SYSTEM
    )
    assert "Diretriz confiável de codificação" in system_texts
    assert "Comando não confiável injetado de fonte externa" not in system_texts


def test_context_planner_collects_preserved_items_only_from_omitted_units() -> None:
    from backend.vtypes.message import ToolCall

    planner = ContextPlanner(memory_store=MemoryStore())

    # Unidade 1 (antiga - será omitida): contém tool_call de assistant e tool_result com erro
    asst_old = VMessage(
        role=MessageRole.ASSISTANT,
        content=[ContentBlock(kind="text", text="Decisão: rodar build")],
        tool_calls=[ToolCall(id="call_build", name="run_build", args={})],
    )
    tool_err_old = VMessage(
        role=MessageRole.TOOL,
        content=[
            ContentBlock(
                kind="text",
                text="Build falhou com code 1 no módulo de compilação " * 20,
            )
        ],
        tool_call_id="call_build",
        is_error=True,
    )

    # Unidade 2 (mais recente - mantida): contém tool_result com sucesso
    user_recent = _msg(MessageRole.USER, "Tentando de novo...")
    asst_recent = VMessage(
        role=MessageRole.ASSISTANT,
        content=[ContentBlock(kind="text", text="Rodando teste")],
        tool_calls=[ToolCall(id="call_test", name="run_test", args={})],
    )
    tool_ok_recent = VMessage(
        role=MessageRole.TOOL,
        content=[ContentBlock(kind="text", text="Testes passaram")],
        tool_call_id="call_test",
    )

    messages = [
        _msg(MessageRole.SYSTEM, "Instruções do sistema"),
        asst_old,
        tool_err_old,
        user_recent,
        asst_recent,
        tool_ok_recent,
    ]

    # Orçamento apertado que só comporta a unidade mais recente
    planned = planner.plan_context(
        messages,
        max_tokens=60,
        enabled=True,
    )

    assert planned.projection is not None
    assert planned.projection.excluded_items_count > 0
    # Decisão e erro da unidade omitida foram coletados e preservados
    assert len(planned.projection.preserved_decisions) >= 1
    assert any("run_build" in d for d in planned.projection.preserved_decisions)
    assert len(planned.projection.preserved_errors) >= 1
    assert any("Build falhou" in e for e in planned.projection.preserved_errors)

    # As decisões da unidade mantida NÃO devem ser declaradas como "preservadas da omissão"
    assert not any("run_test" in d for d in planned.projection.preserved_decisions)

    # Verifica que foram reinjetados no marcador presente em planned.messages
    marker_text = " ".join(
        m.text() for m in planned.messages if m.role == MessageRole.SYSTEM
    )
    assert "Decisões anteriores preservadas:" in marker_text
    assert "run_build" in marker_text
    assert "Erros/falhas anteriores preservados:" in marker_text


def test_context_projection_evidence_keeps_preserved_items() -> None:
    projection = ContextProjection(
        start_seq=2,
        end_seq=8,
        source_transcript_hash="hash",
        tokens_before=100,
        tokens_after=40,
        preserved_decisions=["Chamada de ferramentas: run_build"],
        preserved_errors=["Tool call_build: Build falhou"],
        preserved_approvals=["Aprovação de run_build: aprovado"],
        excluded_items_count=4,
    )

    evidence = projection.to_evidence_entry()

    assert evidence["preserved_decisions"] == ["Chamada de ferramentas: run_build"]
    assert evidence["preserved_errors"] == ["Tool call_build: Build falhou"]
    assert evidence["preserved_approvals"] == ["Aprovação de run_build: aprovado"]
