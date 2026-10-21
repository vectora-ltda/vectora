"""Testes unitários para Context Planner, Projeções de Contexto e Memória."""

from __future__ import annotations

import pytest

from backend.services.context_manifest import (
    ContextBlock,
    ContextManifest,
    ContextProjection,
)
from backend.services.context_planner import (
    ContextPlanner,
    compute_transcript_hash,
    plan_context,
)
from backend.services.memory_store import MemoryStore
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
    assert evidence["source_transcript_hash"] == "hash123"


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
