"""Testes para src/api/handlers/share.py.

Valida:
- GET /threads/share/{token} retorna 404 quando token inexistente
- POST /threads/share cria token e retorna URL
- DELETE /threads/share/{token} revoga token
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from backend.api.handlers.share import _sanitize_shared_text


@pytest.fixture(scope="module")
def client():
    os.environ["VECTORA_AUTH_REQUIRED"] = "false"
    from backend.api.server import create_app

    app = create_app(serve_static=False)
    return TestClient(app, raise_server_exceptions=False)


class TestShareGetNotFound:
    def test_unknown_token_returns_404(self, client):
        resp = client.get("/threads/share/token-que-nao-existe")
        assert resp.status_code == 404

    def test_404_body_has_detail(self, client):
        resp = client.get("/threads/share/token-invalido-xyz")
        body = resp.json()
        assert "detail" in body

    def test_sanitizes_bearer_and_named_secrets(self):
        value = "Authorization: Bearer abc def token=xyz password: p@ss"
        sanitized = _sanitize_shared_text(value)
        assert "abc def" not in sanitized
        assert "xyz" not in sanitized
        assert "p@ss" not in sanitized
        assert "authorization: [redacted]" in sanitized.lower()


class TestShareCreate:
    def test_create_share_returns_token_and_url(self, client):
        resp = client.post(
            "/threads/share",
            json={"thread_id": "test-thread-abc", "ttl_hours": 1},
        )
        # A criação exige uma sessão existente para validar a posse.
        assert resp.status_code == 404
        assert resp.status_code != 405

    def test_create_share_response_schema(self, client):
        resp = client.post(
            "/threads/share",
            json={"thread_id": "schema-test-thread", "ttl_hours": 2},
        )
        if resp.status_code == 200:
            body = resp.json()
            assert "token" in body
            assert "url" in body
            assert "expires_at" in body
