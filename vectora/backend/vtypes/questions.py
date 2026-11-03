"""Contratos persistidos de perguntas estruturadas aguardando o usuário."""

from __future__ import annotations

from typing import Literal, NotRequired, TypedDict

QuestionStatus = Literal["pending", "answered", "cancelled", "expired"]


class StructuredQuestionRecord(TypedDict):
    """Registro JSON durável sem o ``asyncio.Event`` transitório."""

    question_id: str
    thread_id: str
    prompt: str
    options: list[str]
    allow_free_text: bool
    idempotency_key: str
    created_at: str
    answer: NotRequired[str | None]
    cancelled: bool
    status: QuestionStatus
    expires_at: str
