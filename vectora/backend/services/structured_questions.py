"""Coordenação de perguntas estruturadas aguardando resposta humana."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True)
class StructuredQuestion:
    """Pergunta pendente e seu resultado idempotente."""

    question_id: str
    thread_id: str
    prompt: str
    options: list[str]
    allow_free_text: bool
    idempotency_key: str
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    answer: str | None = None
    cancelled: bool = False
    event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)


class StructuredQuestionStore:
    """Store de processo com timeout, cancelamento e criação idempotente."""

    def __init__(self) -> None:
        self._questions: dict[str, StructuredQuestion] = {}
        self._keys: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        thread_id: str,
        prompt: str,
        options: list[str],
        allow_free_text: bool,
        idempotency_key: str,
    ) -> StructuredQuestion:
        async with self._lock:
            existing_id = self._keys.get((thread_id, idempotency_key))
            if existing_id is not None:
                return self._questions[existing_id]
            question = StructuredQuestion(
                question_id=str(uuid.uuid4()),
                thread_id=thread_id,
                prompt=prompt,
                options=list(dict.fromkeys(options)),
                allow_free_text=allow_free_text,
                idempotency_key=idempotency_key,
            )
            self._questions[question.question_id] = question
            self._keys[(thread_id, idempotency_key)] = question.question_id
            return question

    async def get(self, question_id: str, thread_id: str) -> StructuredQuestion | None:
        async with self._lock:
            question = self._questions.get(question_id)
            return (
                question
                if question is not None and question.thread_id == thread_id
                else None
            )

    async def answer(
        self, question_id: str, thread_id: str, value: str
    ) -> StructuredQuestion:
        question = await self.get(question_id, thread_id)
        if question is None:
            raise KeyError("pergunta não encontrada")
        if question.answer is not None or question.cancelled:
            return question
        if value not in question.options and not question.allow_free_text:
            raise ValueError("resposta não pertence às opções permitidas")
        question.answer = value
        question.event.set()
        return question

    async def cancel(self, question_id: str, thread_id: str) -> StructuredQuestion:
        question = await self.get(question_id, thread_id)
        if question is None:
            raise KeyError("pergunta não encontrada")
        question.cancelled = True
        question.event.set()
        return question

    async def wait(self, question: StructuredQuestion, timeout_s: float) -> str | None:
        try:
            await asyncio.wait_for(question.event.wait(), timeout=timeout_s)
        except TimeoutError:
            return None
        if question.cancelled:
            return ""
        return question.answer


structured_question_store = StructuredQuestionStore()
