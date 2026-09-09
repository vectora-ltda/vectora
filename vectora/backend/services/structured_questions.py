"""Coordenação de perguntas estruturadas aguardando resposta humana."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

QuestionStatus = Literal["pending", "answered", "cancelled", "expired"]


def _store_path() -> Path:
    return Path.home() / ".vectora" / "structured_questions.json"


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
    status: QuestionStatus = "pending"
    expires_at: str = ""
    event: asyncio.Event = field(default_factory=asyncio.Event, repr=False)


class StructuredQuestionStore:
    """Store durável com timeout, cancelamento e criação idempotente."""

    def __init__(self) -> None:
        self._questions: dict[str, StructuredQuestion] = {}
        self._keys: dict[tuple[str, str], str] = {}
        self._lock = asyncio.Lock()
        self._load()

    def _load(self) -> None:
        try:
            values = json.loads(_store_path().read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return
        now = datetime.now(UTC)
        for raw in values if isinstance(values, list) else []:
            try:
                question = StructuredQuestion(
                    question_id=str(raw["question_id"]),
                    thread_id=str(raw["thread_id"]),
                    prompt=str(raw["prompt"]),
                    options=[str(item) for item in raw["options"]],
                    allow_free_text=bool(raw["allow_free_text"]),
                    idempotency_key=str(raw["idempotency_key"]),
                    created_at=str(raw["created_at"]),
                    answer=raw.get("answer"),
                    cancelled=bool(raw.get("cancelled", False)),
                    status=raw.get("status", "pending"),
                    expires_at=str(raw.get("expires_at", "")),
                )
            except (KeyError, TypeError, ValueError):
                continue
            if question.status == "pending" and question.expires_at:
                try:
                    if datetime.fromisoformat(question.expires_at) <= now:
                        question.status = "expired"
                except ValueError:
                    question.status = "expired"
            if question.status != "pending":
                question.event.set()
            self._questions[question.question_id] = question
            self._keys[(question.thread_id, question.idempotency_key)] = (
                question.question_id
            )

    async def _save(self) -> None:
        values: list[dict[str, object]] = [
            {
                "question_id": q.question_id,
                "thread_id": q.thread_id,
                "prompt": q.prompt,
                "options": q.options,
                "allow_free_text": q.allow_free_text,
                "idempotency_key": q.idempotency_key,
                "created_at": q.created_at,
                "answer": q.answer,
                "cancelled": q.cancelled,
                "status": q.status,
                "expires_at": q.expires_at,
            }
            for q in self._questions.values()
        ]
        path = _store_path()
        await asyncio.to_thread(self._write, path, values)

    @staticmethod
    def _write(path: Path, values: list[dict[str, object]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(values, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

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
            question.expires_at = (
                datetime.fromisoformat(question.created_at) + timedelta(hours=1)
            ).isoformat()
            self._questions[question.question_id] = question
            self._keys[(thread_id, idempotency_key)] = question.question_id
            await self._save()
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
        if question.status != "pending":
            return question
        if value not in question.options and not question.allow_free_text:
            raise ValueError("resposta não pertence às opções permitidas")
        question.answer = value
        question.status = "answered"
        question.event.set()
        await self._save()
        return question

    async def cancel(self, question_id: str, thread_id: str) -> StructuredQuestion:
        question = await self.get(question_id, thread_id)
        if question is None:
            raise KeyError("pergunta não encontrada")
        if question.status == "pending":
            question.cancelled = True
            question.status = "cancelled"
        question.event.set()
        await self._save()
        return question

    async def wait(self, question: StructuredQuestion, timeout_s: float) -> str | None:
        try:
            await asyncio.wait_for(question.event.wait(), timeout=timeout_s)
        except TimeoutError:
            async with self._lock:
                if question.status == "pending":
                    question.status = "expired"
                    question.expires_at = datetime.now(UTC).isoformat()
                    await self._save()
            return None
        if question.status == "expired":
            return None
        if question.cancelled:
            return ""
        return question.answer


structured_question_store = StructuredQuestionStore()
