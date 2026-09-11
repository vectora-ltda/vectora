"""GET /threads/{thread_id}/history.

Cobre: paginação por offset/limit, has_more, MESSAGES_CAP, erro de thread inexistente.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

os.environ.setdefault("VECTORA_AUTH_REQUIRED", "false")


def _make_app() -> Any:
    from backend.api.server import create_app

    return create_app()


def _fake_thread():
    from backend.api.schemas import Thread

    return Thread(id="t1", created_at="", updated_at="", workspace_id="")


def _patch_get_thread():
    return patch(
        "backend.api.handlers.threads.get_thread",
        new_callable=AsyncMock,
        return_value=_fake_thread(),
    )


def _pairs(n: int, start: int = 0) -> list[tuple[str, str, str, list]]:
    """Gera n quadras (role, text, checkpoint_id, attachments_meta) alternando human/assistant."""
    out = []
    for i in range(n):
        role = "human" if i % 2 == 0 else "assistant"
        out.append((role, f"message {start + i}", f"cp{start + i}", []))
    return out


def _request_for_user(user_id: str) -> Request:
    """Cria request autenticado mínimo para testar a fronteira de posse."""
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/threads/t1/history",
            "headers": [],
            "scheme": "http",
            "server": ("test", 80),
            "client": ("test", 1),
            "root_path": "",
            "query_string": b"",
        }
    )
    request.state.user = type("User", (), {"id": user_id})()
    return request


# ---------------------------------------------------------------------------
# GET /threads/{thread_id}/history — paginação básica
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_retorna_todas_quando_menos_de_200() -> None:
    """Menos de 200 mensagens → has_more=False, retorna todas."""
    pairs = _pairs(10)
    with (
        _patch_get_thread(),
        patch(
            "backend.services.agent_factory.aget_thread_messages",
            new_callable=AsyncMock,
            return_value=pairs,
        ),
    ):
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/threads/t1/history")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 10
    assert data["has_more"] is False
    assert data["total_count"] == 10


@pytest.mark.asyncio
async def test_history_limita_a_limit_quando_especificado() -> None:
    """limit=5 retorna só 5 mensagens (mais recentes)."""
    pairs = _pairs(20)
    with (
        _patch_get_thread(),
        patch(
            "backend.services.agent_factory.aget_thread_messages",
            new_callable=AsyncMock,
            return_value=pairs,
        ),
    ):
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/threads/t1/history?limit=5")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 5
    assert data["has_more"] is True
    assert data["total_count"] == 20


@pytest.mark.asyncio
async def test_history_offset_pula_mensagens_recentes() -> None:
    """offset=5 com limit=5 retorna mensagens mais antigas."""
    pairs = _pairs(20)
    with (
        _patch_get_thread(),
        patch(
            "backend.services.agent_factory.aget_thread_messages",
            new_callable=AsyncMock,
            return_value=pairs,
        ),
    ):
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/threads/t1/history?limit=5&offset=5")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 5
    # Mensagens com offset=5 vêm do índice 15 ao 19 (mais antigas)
    assert data["has_more"] is True
    assert data["total_count"] == 20


@pytest.mark.asyncio
async def test_history_cap_200_quando_sem_limit() -> None:
    """Sem limit especificado, cap é 200 mensagens mais recentes."""
    pairs = _pairs(250)
    with (
        _patch_get_thread(),
        patch(
            "backend.services.agent_factory.aget_thread_messages",
            new_callable=AsyncMock,
            return_value=pairs,
        ),
    ):
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/threads/t1/history")

    assert resp.status_code == 200
    data = resp.json()
    assert len(data["messages"]) == 200
    assert data["has_more"] is True
    assert data["total_count"] == 250


@pytest.mark.asyncio
async def test_history_thread_sem_mensagens_retorna_vazio() -> None:
    """Thread sem mensagens → lista vazia, has_more=False."""
    with (
        _patch_get_thread(),
        patch(
            "backend.services.agent_factory.aget_thread_messages",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/threads/t1/history")

    assert resp.status_code == 200
    data = resp.json()
    assert data["messages"] == []
    assert data["has_more"] is False
    assert data["total_count"] == 0


@pytest.mark.asyncio
async def test_history_mensagens_na_ordem_cronologica() -> None:
    """Mensagens retornadas na ordem mais-antiga→mais-recente."""
    pairs = [
        ("human", "first", "cp0", []),
        ("assistant", "second", "cp1", []),
        ("human", "third", "cp2", []),
    ]
    with (
        _patch_get_thread(),
        patch(
            "backend.services.agent_factory.aget_thread_messages",
            new_callable=AsyncMock,
            return_value=pairs,
        ),
    ):
        app = _make_app()
        client = TestClient(app)
        resp = client.get("/threads/t1/history")

    data = resp.json()
    texts = [m["content"] for m in data["messages"]]
    assert texts == ["first", "second", "third"]


@pytest.mark.asyncio
async def test_history_exige_posse_para_usuario_autenticado() -> None:
    """Usuário diferente do dono recebe 404 sem carregar mensagens."""
    store = AsyncMock()
    store.get_session.return_value = {"user_id": "owner"}
    with (
        patch(
            "backend.api.handlers.threads._get_session_store",
            new=AsyncMock(return_value=store),
        ),
        patch(
            "backend.services.agent_factory.aget_thread_messages",
            new_callable=AsyncMock,
        ) as load_history,
    ):
        from fastapi import HTTPException

        from backend.api.handlers.threads import get_thread_history_paginated

        with pytest.raises(HTTPException) as exc:
            await get_thread_history_paginated("t1", _request_for_user("other"))

    assert exc.value.status_code == 404
    load_history.assert_not_awaited()


@pytest.mark.asyncio
async def test_history_propaga_404_de_thread_inexistente() -> None:
    """Thread inexistente não vira página vazia com status 200."""
    from fastapi import HTTPException

    from backend.api.handlers.threads import get_thread_history_paginated

    with (
        patch("backend.api.handlers.threads._assert_owns_thread", new=AsyncMock()),
        patch(
            "backend.api.handlers.threads.get_thread",
            new=AsyncMock(side_effect=HTTPException(status_code=404)),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await get_thread_history_paginated("missing", _request_for_user("owner"))

    assert exc.value.status_code == 404
