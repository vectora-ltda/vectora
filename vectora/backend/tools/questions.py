"""Tool para solicitar uma decisão humana com opções estruturadas."""

from __future__ import annotations

import json

from backend.engine.stream_events import StructuredQuestionRequested
from backend.services.structured_questions import structured_question_store
from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool


@vtool(extras=ToolExtras(category="interaction", icon="circle-help"))
async def ask_structured_question(
    prompt: str,
    ctx: ToolContext,
    options: list[str] | None = None,
    allow_free_text: bool = True,
    idempotency_key: str = "",
    timeout_seconds: float = 300.0,
) -> str:
    """Solicita ao usuário uma escolha ou resposta livre, sem duplicar pedidos."""
    if not ctx.thread_id:
        return json.dumps({"status": "error", "error": "thread_id ausente"})
    normalized_options = options or []
    if not prompt.strip() or not (1 <= len(normalized_options) <= 20):
        return json.dumps({"status": "error", "error": "prompt e opções inválidos"})
    key = idempotency_key.strip() or ctx.tool_call_id or prompt.strip()
    effective_timeout = max(1.0, min(timeout_seconds, 3600.0))
    question = await structured_question_store.create(
        ctx.thread_id,
        prompt.strip(),
        normalized_options,
        allow_free_text,
        key,
        effective_timeout,
    )
    sink = ctx._extra.get("event_sink")
    if sink is not None and question.status == "pending":
        await sink(
            StructuredQuestionRequested(
                question_id=question.question_id,
                thread_id=ctx.thread_id,
                prompt=question.prompt,
                options=question.options,
                allow_free_text=question.allow_free_text,
                expires_at=question.expires_at,
            )
        )
    answer = await structured_question_store.wait(question, effective_timeout)
    if answer is None:
        return json.dumps({"status": "timeout", "question_id": question.question_id})
    if answer == "":
        return json.dumps({"status": "cancelled", "question_id": question.question_id})
    return json.dumps(
        {"status": "answered", "question_id": question.question_id, "answer": answer},
        ensure_ascii=False,
    )
