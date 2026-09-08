from __future__ import annotations

import asyncio

import pytest

from backend.services.structured_questions import StructuredQuestionStore


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
    assert await task == ""


@pytest.mark.asyncio
async def test_rejeita_opcao_fora_do_contrato() -> None:
    store = StructuredQuestionStore()
    question = await store.create("thread", "Escolha", ["a"], False, "invalid")
    with pytest.raises(ValueError):
        await store.answer(question.question_id, "thread", "c")
