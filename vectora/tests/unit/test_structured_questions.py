from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.services import structured_questions
from backend.services.structured_questions import StructuredQuestionStore


@pytest.fixture(autouse=True)
def isolated_question_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        structured_questions,
        "_store_path",
        lambda: tmp_path / "structured_questions.json",
    )


@pytest.mark.asyncio
async def test_pergunta_idempotente_resposta_e_cancelamento() -> None:
    store = StructuredQuestionStore()
    question = await store.create("thread", "Escolha", ["a", "b"], False, "k")
    duplicate = await store.create("thread", "outra", ["x"], True, "k")
    assert duplicate.question_id == question.question_id
    await store.answer(question.question_id, "thread", "b")
    assert await store.wait(question, 0.01) == "b"


@pytest.mark.asyncio
async def test_pergunta_expira_e_cancelamento_desperta() -> None:
    store = StructuredQuestionStore()
    question = await store.create("thread", "Escolha", ["a"], False, "timeout")
    assert await store.wait(question, 0.01) is None
    task = asyncio.create_task(store.wait(question, 1.0))
    await store.cancel(question.question_id, "thread")
    assert await task is None


@pytest.mark.asyncio
async def test_rejeita_opcao_fora_do_contrato() -> None:
    store = StructuredQuestionStore()
    question = await store.create("thread", "Escolha", ["a"], False, "invalid")
    with pytest.raises(ValueError):
        await store.answer(question.question_id, "thread", "c")


@pytest.mark.asyncio
async def test_prazo_persistido_corresponde_ao_timeout_da_pergunta() -> None:
    store = StructuredQuestionStore()
    before = datetime.now(UTC)
    question = await store.create("thread", "Escolha", ["a"], False, "prazo", 7.0)
    expires_at = datetime.fromisoformat(question.expires_at)
    assert 6.0 <= (expires_at - before).total_seconds() <= 8.0


@pytest.mark.asyncio
async def test_resposta_e_cancelamento_concorrentes_tem_uma_transicao() -> None:
    store = StructuredQuestionStore()
    question = await store.create("thread", "Escolha", ["a"], False, "corrente")
    answered, cancelled = await asyncio.gather(
        store.answer(question.question_id, "thread", "a"),
        store.cancel(question.question_id, "thread"),
    )
    assert answered.status == cancelled.status
    assert answered.status in {"answered", "cancelled"}


@pytest.mark.asyncio
async def test_persistencia_reidrata_pergunta_e_marca_expirada(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arquivo = tmp_path / "structured_questions.json"
    monkeypatch.setattr(structured_questions, "_store_path", lambda: arquivo)
    store = StructuredQuestionStore()
    question = await store.create("thread", "Escolha", ["a"], False, "persistente")
    question.status = "expired"
    await store._save()

    reidratado = StructuredQuestionStore()
    restored = await reidratado.create("thread", "outra", ["b"], False, "persistente")
    assert restored.question_id == question.question_id
    assert restored.status == "expired"


@pytest.mark.asyncio
async def test_pergunta_pendente_orfa_do_restart_expira_com_seguranca(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    arquivo = tmp_path / "structured_questions.json"
    monkeypatch.setattr(structured_questions, "_store_path", lambda: arquivo)
    store = StructuredQuestionStore()
    question = await store.create("thread", "Escolha", ["a"], False, "restart")
    reidratado = StructuredQuestionStore()
    restored = await reidratado.get(question.question_id, "thread")
    assert restored is not None
    assert restored.status == "expired"
