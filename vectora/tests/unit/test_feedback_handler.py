from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from backend.api.handlers.feedback import FeedbackRequest, submit_feedback


def test_feedback_rejeita_campos_desconhecidos() -> None:
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(
            {"kind": "bug", "description": "falha", "inesperado": "valor"}
        )


@pytest.mark.asyncio
async def test_feedback_filtra_contexto_e_associa_usuario(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("backend.api.handlers.feedback.settings.vectora_home", tmp_path)
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/feedback",
            "headers": [],
            "query_string": b"",
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
            "root_path": "",
        }
    )
    request.state.user = SimpleNamespace(id="alice")
    response = await submit_feedback(
        request,
        FeedbackRequest(
            kind="bug",
            description="falha",
            include_context=True,
            context={"route": "/chat", "token": "secret"},
        ),
    )
    record = json.loads((tmp_path / "feedback.jsonl").read_text(encoding="utf-8"))
    assert response.id == record["id"]
    assert record["user_id"] == "alice"
    assert record["context"] == {"route": "/chat"}
