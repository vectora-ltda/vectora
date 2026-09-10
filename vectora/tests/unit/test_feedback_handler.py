from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.middleware.base import RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from backend.api.handlers.feedback import FeedbackRequest, submit_feedback


def test_feedback_rejeita_campos_desconhecidos() -> None:
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(
            {"kind": "bug", "description": "falha", "inesperado": "valor"}
        )


def test_feedback_rejeita_descricao_apenas_com_espacos() -> None:
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate({"kind": "bug", "description": "   "})


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
            context={"route": "/chat"},
        ),
    )
    record = json.loads((tmp_path / "feedback.jsonl").read_text(encoding="utf-8"))
    assert response.id == record["id"]
    assert record["user_id"] == "alice"
    assert record["context"] == {"route": "/chat"}


def test_feedback_rejeita_chave_de_contexto_desconhecida() -> None:
    with pytest.raises(ValidationError):
        FeedbackRequest.model_validate(
            {
                "kind": "bug",
                "description": "falha",
                "context": {"token": "secret"},
            }
        )


def test_feedback_descarta_valor_de_contexto_nao_tecnico() -> None:
    from backend.api.handlers.feedback import _safe_context

    body = FeedbackRequest(
        kind="bug",
        description="falha",
        include_context=True,
        context={"route": "/chat"},
    )
    body.context["route"] = "segredo da conversa"
    assert _safe_context(body) == {}


def test_feedback_http_isola_rate_limit_por_usuario(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend.api.middleware.auth import AuthMiddleware
    from backend.api.server import create_app

    async def fake_dispatch(
        _middleware: AuthMiddleware,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request.state.user = SimpleNamespace(
            id=request.headers.get("x-test-user", "alice")
        )
        return await call_next(request)

    monkeypatch.setattr(AuthMiddleware, "dispatch", fake_dispatch)
    monkeypatch.setattr("backend.api.handlers.feedback.settings.vectora_home", tmp_path)
    client = TestClient(create_app(serve_static=False), raise_server_exceptions=False)
    payload = {"kind": "bug", "description": "falha"}

    for _ in range(5):
        assert (
            client.post(
                "/feedback", json=payload, headers={"x-test-user": "alice"}
            ).status_code
            == 200
        )
    assert (
        client.post(
            "/feedback", json=payload, headers={"x-test-user": "alice"}
        ).status_code
        == 429
    )
    assert (
        client.post(
            "/feedback", json=payload, headers={"x-test-user": "bob"}
        ).status_code
        == 200
    )
