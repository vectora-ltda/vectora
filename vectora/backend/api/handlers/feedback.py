"""Feedback inline autenticado, limitado e sem conteúdo sensível."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.api.middleware.rate_limit import limiter
from backend.settings import settings

router = APIRouter(prefix="/feedback", tags=["feedback"])


class FeedbackRequest(BaseModel):
    kind: Literal["bug", "suggestion"]
    description: str = Field(min_length=1, max_length=5000)
    include_context: bool = False
    context: dict[str, str] = Field(default_factory=dict)


class FeedbackResponse(BaseModel):
    id: str
    status: Literal["received"] = "received"


def _user_id(request: Request) -> str:
    user = getattr(request.state, "user", None)
    if user is None or not getattr(user, "id", None):
        raise HTTPException(status_code=401, detail="Autenticação necessária")
    return str(user.id)


@router.post("", response_model=FeedbackResponse)
@limiter.limit("5/minute")
async def submit_feedback(request: Request, body: FeedbackRequest) -> FeedbackResponse:
    """Recebe feedback mínimo e grava somente metadados permitidos."""
    user_id = _user_id(request)
    safe_context = {
        key: value[:200]
        for key, value in body.context.items()
        if body.include_context and key in {"app_version", "platform", "route"}
    }
    record = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "kind": body.kind,
        "description": body.description,
        "context": safe_context,
        "created_at": datetime.now(UTC).isoformat(),
    }
    path = settings.vectora_home / "feedback.jsonl"

    def persist() -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    await asyncio.to_thread(persist)
    return FeedbackResponse(id=record["id"])
