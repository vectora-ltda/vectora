"""Remember — gatilho automático a cada N turnos (backend/services/remember_trigger.py)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from backend.services.learning import DistillationResult, SkillDraft
from backend.services.remember_trigger import (
    REMEMBER_TRIGGER_EVERY_N_TURNS,
    maybe_trigger_remember,
)
from backend.settings import Settings


@pytest.fixture
def _no_pending(monkeypatch):
    monkeypatch.setattr(
        "backend.api.handlers.threads.get_remember_pending",
        AsyncMock(return_value=False),
    )


@pytest.fixture(autouse=True)
def _provider_configured(monkeypatch):
    """Hermético: os testes de "caminho feliz" não podem depender de haver
    (ou não) uma API key real na máquina que roda a suíte — fixam um
    provider configurado por padrão. `test_gatilho_pula_sem_provider...`
    abaixo sobrescreve isso explicitamente pra testar o caso vazio."""
    monkeypatch.setattr(
        Settings, "configured_llm_providers", lambda self: ["google-genai"]
    )


@pytest.mark.asyncio
async def test_not_a_multiple_of_n_does_not_distill(monkeypatch, _no_pending):
    monkeypatch.setattr(
        "backend.api.handlers.threads.increment_remember_turn_count",
        AsyncMock(return_value=REMEMBER_TRIGGER_EVERY_N_TURNS - 1),
    )
    distill = AsyncMock()
    monkeypatch.setattr("backend.services.learning.distill_transcript", distill)

    await maybe_trigger_remember("t1", "u1")

    distill.assert_not_called()


@pytest.mark.asyncio
async def test_pending_proposal_blocks_new_trigger(monkeypatch):
    monkeypatch.setattr(
        "backend.api.handlers.threads.increment_remember_turn_count",
        AsyncMock(return_value=REMEMBER_TRIGGER_EVERY_N_TURNS),
    )
    monkeypatch.setattr(
        "backend.api.handlers.threads.get_remember_pending",
        AsyncMock(return_value=True),
    )
    distill = AsyncMock()
    monkeypatch.setattr("backend.services.learning.distill_transcript", distill)

    await maybe_trigger_remember("t1", "u1")

    distill.assert_not_called()


@pytest.mark.asyncio
async def test_multiple_of_n_without_pending_distills_and_writes_proposal(
    monkeypatch, _no_pending
):
    monkeypatch.setattr(
        "backend.api.handlers.threads.increment_remember_turn_count",
        AsyncMock(return_value=REMEMBER_TRIGGER_EVERY_N_TURNS),
    )
    from backend.services import agent_factory

    monkeypatch.setattr(
        agent_factory,
        "aget_thread_messages",
        AsyncMock(return_value=[("human", "gosto de respostas curtas", "", [])]),
    )
    monkeypatch.setattr(
        "backend.services.learning.distill_transcript",
        AsyncMock(
            return_value=DistillationResult(
                skills=[SkillDraft(name="Nova", description="d", content="c")],
                facts=["fato durável"],
            )
        ),
    )
    monkeypatch.setattr("backend.workspace.skills.list_skills", lambda user_id: [])
    monkeypatch.setattr(
        "backend.tools.memory.list_fact_contents", AsyncMock(return_value=[])
    )
    set_pending = AsyncMock()
    monkeypatch.setattr(
        "backend.api.handlers.threads.set_remember_pending", set_pending
    )
    artifact_calls: list[dict] = []

    async def _fake_create_artifact(
        *, artifact_type: str, title: str, content: str, ctx: object
    ) -> str:
        artifact_calls.append(
            {
                "artifact_type": artifact_type,
                "title": title,
                "content": content,
                "ctx": ctx,
            }
        )
        return "{}"

    monkeypatch.setattr("backend.tools.fs.create_artifact", _fake_create_artifact)

    await maybe_trigger_remember("t1", "u1")

    set_pending.assert_called_once_with("t1", True)
    assert artifact_calls[0]["artifact_type"] == "remember_proposal"
    assert "Nova" in artifact_calls[0]["content"]
    assert "fato durável" in artifact_calls[0]["content"]


@pytest.mark.asyncio
async def test_nothing_reusable_does_not_mark_pending_nor_write_artifact(
    monkeypatch, _no_pending
):
    """Erro/borda: sem nada reaproveitável no transcript, não marca pending
    nem grava artifact — resultado vazio é válido, não gera ruído."""
    monkeypatch.setattr(
        "backend.api.handlers.threads.increment_remember_turn_count",
        AsyncMock(return_value=REMEMBER_TRIGGER_EVERY_N_TURNS),
    )
    from backend.services import agent_factory

    monkeypatch.setattr(
        agent_factory,
        "aget_thread_messages",
        AsyncMock(return_value=[("human", "oi", "", [])]),
    )
    monkeypatch.setattr(
        "backend.services.learning.distill_transcript",
        AsyncMock(return_value=DistillationResult()),
    )
    monkeypatch.setattr("backend.workspace.skills.list_skills", lambda user_id: [])
    monkeypatch.setattr(
        "backend.tools.memory.list_fact_contents", AsyncMock(return_value=[])
    )
    set_pending = AsyncMock()
    monkeypatch.setattr(
        "backend.api.handlers.threads.set_remember_pending", set_pending
    )

    await maybe_trigger_remember("t1", "u1")

    set_pending.assert_not_called()


@pytest.mark.asyncio
async def test_empty_transcript_returns_without_distilling(monkeypatch, _no_pending):
    """Erro/borda: thread sem nenhuma mensagem ainda (transcript vazio) não
    chama distill_transcript à toa."""
    monkeypatch.setattr(
        "backend.api.handlers.threads.increment_remember_turn_count",
        AsyncMock(return_value=REMEMBER_TRIGGER_EVERY_N_TURNS),
    )
    from backend.services import agent_factory

    monkeypatch.setattr(
        agent_factory, "aget_thread_messages", AsyncMock(return_value=[])
    )
    distill = AsyncMock()
    monkeypatch.setattr("backend.services.learning.distill_transcript", distill)

    await maybe_trigger_remember("t1", "u1")

    distill.assert_not_called()


@pytest.mark.asyncio
async def test_fato_ja_salvo_nao_e_proposto_de_novo(monkeypatch, _no_pending):
    """Regressão: um fato já aprovado/salvo em sessão anterior não pode
    voltar a ser proposto pelo Remember — sem o dedup, o mesmo fato
    reaparece a cada N turnos indefinidamente."""
    monkeypatch.setattr(
        "backend.api.handlers.threads.increment_remember_turn_count",
        AsyncMock(return_value=REMEMBER_TRIGGER_EVERY_N_TURNS),
    )
    from backend.services import agent_factory

    monkeypatch.setattr(
        agent_factory,
        "aget_thread_messages",
        AsyncMock(return_value=[("human", "prefiro respostas em PT-BR", "", [])]),
    )
    monkeypatch.setattr(
        "backend.services.learning.distill_transcript",
        AsyncMock(
            return_value=DistillationResult(
                skills=[],
                facts=["Usuário prefere respostas em PT-BR", "Fato genuinamente novo"],
            )
        ),
    )
    monkeypatch.setattr("backend.workspace.skills.list_skills", lambda user_id: [])
    monkeypatch.setattr(
        "backend.tools.memory.list_fact_contents",
        AsyncMock(return_value=["usuário prefere respostas em pt-br"]),
    )
    set_pending = AsyncMock()
    monkeypatch.setattr(
        "backend.api.handlers.threads.set_remember_pending", set_pending
    )
    artifact_calls: list[dict] = []

    async def _fake_create_artifact(
        *, artifact_type: str, title: str, content: str, ctx: object
    ) -> str:
        artifact_calls.append(
            {
                "artifact_type": artifact_type,
                "title": title,
                "content": content,
                "ctx": ctx,
            }
        )
        return "{}"

    monkeypatch.setattr("backend.tools.fs.create_artifact", _fake_create_artifact)

    await maybe_trigger_remember("t1", "u1")

    content = artifact_calls[0]["content"]
    assert "Fato genuinamente novo" in content
    assert "Usuário prefere respostas em PT-BR" not in content


@pytest.mark.asyncio
async def test_gatilho_pula_sem_provider_configurado_mas_dispara_com_um(
    monkeypatch, _no_pending
):
    """Gatilho automático (fire-and-forget, sem usuário no loop) nunca pode
    chamar uma LLM sem nenhum provider configurado — ex.: instalação recém-
    extraída ainda sem setup. Caso de borda no mesmo teste: com exatamente
    1 provider configurado, o gatilho volta a funcionar normalmente."""
    monkeypatch.setattr(
        "backend.api.handlers.threads.increment_remember_turn_count",
        AsyncMock(return_value=REMEMBER_TRIGGER_EVERY_N_TURNS),
    )
    distill = AsyncMock(return_value=DistillationResult())
    monkeypatch.setattr("backend.services.learning.distill_transcript", distill)
    # Mensagens reais nas DUAS chamadas — sem isso o teste passaria mesmo
    # sem a guarda, só porque a thread não tem histórico de verdade e o
    # early-return de "transcript vazio" mascararia o comportamento real.
    from backend.services import agent_factory

    monkeypatch.setattr(
        agent_factory,
        "aget_thread_messages",
        AsyncMock(return_value=[("human", "oi", "", [])]),
    )

    monkeypatch.setattr(Settings, "configured_llm_providers", lambda self: [])
    await maybe_trigger_remember("t1", "u1")
    distill.assert_not_called()

    monkeypatch.setattr(
        Settings, "configured_llm_providers", lambda self: ["google-genai"]
    )
    await maybe_trigger_remember("t1", "u1")
    distill.assert_called_once()


@pytest.mark.asyncio
async def test_exception_never_propagates(monkeypatch):
    """Best-effort: qualquer falha interna nunca propaga (nunca pode afetar
    o turno de chat que já terminou)."""
    monkeypatch.setattr(
        "backend.api.handlers.threads.increment_remember_turn_count",
        AsyncMock(side_effect=RuntimeError("banco indisponível")),
    )

    await maybe_trigger_remember("t1", "u1")  # não deve levantar
