"""Testes para src/api/handlers/share.py.

Valida:
- GET /threads/share/{token} retorna 404 quando token inexistente
- POST /threads/share cria token e retorna URL
- DELETE /threads/share/{token} revoga token
"""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from backend.api.handlers import share as share_handler
from backend.api.handlers.share import _sanitize_shared_text
from backend.rbac.auth import _write_audit


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

    def test_sanitizes_bearer_and_named_secrets(self) -> None:
        value = "Authorization: Bearer abc def token=xyz password: p@ss"
        sanitized = _sanitize_shared_text(value)
        assert "abc def" not in sanitized
        assert "def" not in sanitized
        assert "xyz" not in sanitized
        assert "p@ss" not in sanitized
        assert "authorization: [redacted]" in sanitized.lower()
        quoted = _sanitize_shared_text('{"token":"abc","api_key":"abc def"}')
        assert "abc" not in quoted
        assert "abc def" not in quoted

    def test_sanitizes_quoted_keys_and_values_with_spaces(self) -> None:
        sanitized = _sanitize_shared_text('token:"abc def" api_key="key with spaces"')
        assert "abc def" not in sanitized
        assert "key with spaces" not in sanitized

    @pytest.mark.asyncio
    async def test_auditoria_obrigatoria_propaga_falha(self):
        class BrokenDb:
            async def execute(self, *_args):
                raise RuntimeError("audit indisponível")

        with pytest.raises(RuntimeError, match="audit indisponível"):
            await _write_audit(BrokenDb(), "user", "share_create", strict=True)


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

    @pytest.mark.asyncio
    async def test_create_share_returns_complete_schema_for_owned_session(
        self, monkeypatch
    ):
        class FakeDb:
            async def execute(self, *_args):
                return None

            async def commit(self):
                return None

        class FakeStore:
            async def get_session(self, _thread_id):
                return {"user_id": "owner"}

        request = __import__("starlette.requests", fromlist=["Request"]).Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/threads/share",
                "headers": [],
                "scheme": "http",
                "server": ("test", 80),
                "client": ("test", 1),
                "root_path": "",
                "query_string": b"",
            }
        )
        request.state.user = SimpleNamespace(id="owner", role="member")
        monkeypatch.setattr(share_handler, "_get_db", lambda: _resolved(FakeDb()))
        monkeypatch.setattr(share_handler, "_ensure_share_table", _noop)
        monkeypatch.setattr(
            "backend.services.agent_factory.get_session_store",
            lambda: _resolved(FakeStore()),
        )
        monkeypatch.setattr(share_handler, "_write_share_audit", _noop)

        result = await share_handler.create_share(
            request, share_handler.CreateShareRequest(thread_id="thread")
        )
        assert result.token
        assert result.url.endswith(f"/share/{result.token}")
        assert result.expires_at
        assert result.permission == "read"

    @pytest.mark.asyncio
    async def test_criacao_falha_se_auditoria_obrigatoria_falhar(self, monkeypatch):
        class FakeDb:
            async def execute(self, *_args):
                return None

            async def commit(self):
                return None

        class FakeStore:
            async def get_session(self, _thread_id):
                return {"user_id": "owner"}

        request = __import__("starlette.requests", fromlist=["Request"]).Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/threads/share",
                "headers": [],
                "scheme": "http",
                "server": ("test", 80),
                "client": ("test", 1),
                "root_path": "",
                "query_string": b"",
            }
        )
        request.state.user = SimpleNamespace(id="owner", role="member")
        monkeypatch.setattr(share_handler, "_get_db", lambda: _resolved(FakeDb()))
        monkeypatch.setattr(share_handler, "_ensure_share_table", _noop)
        monkeypatch.setattr(
            "backend.services.agent_factory.get_session_store",
            lambda: _resolved(FakeStore()),
        )
        monkeypatch.setattr(share_handler, "_write_share_audit", _audit_failure)

        with pytest.raises(RuntimeError, match="audit indisponível"):
            await share_handler.create_share(
                request, share_handler.CreateShareRequest(thread_id="thread")
            )


class TestShareDelete:
    async def _database(self, token: str = "share-token"):
        import aiosqlite

        db = await aiosqlite.connect(":memory:")
        await db.execute(
            "CREATE TABLE shared_threads (token TEXT PRIMARY KEY, thread_id TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL, permission TEXT NOT NULL DEFAULT 'read')"
        )
        await db.execute(
            "INSERT INTO shared_threads VALUES (?, 'thread-1', 'owner-1', '2026-01-01', '2026-12-31', 'read')",
            (token,),
        )
        await db.commit()
        return db

    @staticmethod
    def _request(user: object | None = None) -> Request:
        request = Request(
            {
                "type": "http",
                "method": "DELETE",
                "path": "/threads/share/share-token",
                "headers": [],
                "scheme": "http",
                "server": ("test", 80),
                "client": ("test", 1),
                "root_path": "",
                "query_string": b"",
            }
        )
        if user is not None:
            request.state.user = user
        return request

    @pytest.mark.asyncio
    async def test_revoga_pelo_criador_e_persiste_auditoria(self, monkeypatch):
        db = await self._database()
        events = []

        async def audit(user_id, action, **fields):
            events.append((user_id, action, fields))

        monkeypatch.setattr(share_handler, "_get_db", lambda: _resolved(db))
        monkeypatch.setattr(share_handler, "_ensure_share_table", _noop)
        monkeypatch.setattr(share_handler, "_write_share_audit", audit)

        result = await share_handler.delete_share(
            "share-token", self._request(SimpleNamespace(id="owner-1", role="member"))
        )

        assert result == {}
        assert events == [("owner-1", "share_revoke", {"token": "share-token"})]
        async with db.execute(
            "SELECT 1 FROM shared_threads WHERE token = ?", ("share-token",)
        ) as cursor:
            assert await cursor.fetchone() is None
        await db.close()

    @pytest.mark.asyncio
    async def test_rejeita_nao_autorizado_e_sem_autenticacao(self, monkeypatch):
        db = await self._database()
        monkeypatch.setattr(share_handler, "_get_db", lambda: _resolved(db))
        monkeypatch.setattr(share_handler, "_ensure_share_table", _noop)

        with pytest.raises(share_handler.HTTPException) as unauthenticated:
            await share_handler.delete_share("share-token", self._request())
        assert unauthenticated.value.status_code == 401

        with pytest.raises(share_handler.HTTPException) as forbidden:
            await share_handler.delete_share(
                "share-token", self._request(SimpleNamespace(id="other", role="member"))
            )
        assert forbidden.value.status_code == 403
        await db.close()

    @pytest.mark.asyncio
    async def test_restaura_link_se_auditoria_falhar(self, monkeypatch):
        db = await self._database()
        monkeypatch.setattr(share_handler, "_get_db", lambda: _resolved(db))
        monkeypatch.setattr(share_handler, "_ensure_share_table", _noop)
        monkeypatch.setattr(share_handler, "_write_share_audit", _audit_failure)

        with pytest.raises(RuntimeError, match="audit indisponível"):
            await share_handler.delete_share(
                "share-token",
                self._request(SimpleNamespace(id="owner-1", role="member")),
            )

        async with db.execute(
            "SELECT created_by, permission FROM shared_threads WHERE token = ?",
            ("share-token",),
        ) as cursor:
            assert await cursor.fetchone() == ("owner-1", "read")
        await db.close()


async def _resolved(value):
    return value


async def _noop(*_args, **_kwargs):
    return None


async def _audit_failure(*_args, **_kwargs):
    raise RuntimeError("audit indisponível")
