"""Endpoints de username — disponibilidade + signup por username.

Fixture função-a-função com banco temporário isolado (diferente do
module-scoped de test_api_auth.py) porque estes testes dependem de estado de
usuários específico (livre vs. em uso).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_api_auth_username.db")
    monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "true")

    import aiosqlite

    import backend.rbac.auth as auth_mod

    monkeypatch.setattr(auth_mod, "_db_conn", None)
    monkeypatch.setattr(auth_mod, "_get_secret", lambda: "api-username-test-secret-xx")

    async def _patched_get_db():
        if auth_mod._db_conn is not None:
            return auth_mod._db_conn
        conn = await aiosqlite.connect(db_file)
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        await auth_mod._ensure_schema(conn)
        auth_mod._db_conn = conn
        return conn

    monkeypatch.setattr(auth_mod, "_get_db", _patched_get_db)

    from backend.api.server import create_app

    app = create_app(serve_static=False)
    yield TestClient(app, raise_server_exceptions=False)

    async def _close():
        if auth_mod._db_conn is not None:
            await auth_mod._db_conn.close()
            auth_mod._db_conn = None

    asyncio.run(_close())


def test_available_true_em_banco_vazio(client):
    r = client.get("/auth/username-available", params={"username": "bruno"})
    assert r.status_code == 200
    assert r.json() == {"normalized": "bruno", "available": True, "suggestion": "bruno"}


def test_normaliza_a_consulta(client):
    r = client.get("/auth/username-available", params={"username": "  Bruno! "})
    assert r.status_code == 200
    assert r.json()["normalized"] == "bruno"


def test_available_false_com_sugestao_quando_em_uso(client):
    # Primeiro usuário (root) fica com username "bruno".
    r = client.post(
        "/auth/signup",
        json={"email": "b@x.com", "password": "12345678", "name": "Bruno"},
    )
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "bruno"

    r = client.get("/auth/username-available", params={"username": "bruno"})
    body = r.json()
    assert body["available"] is False
    assert body["suggestion"].startswith("bruno#")
    assert body["suggestion"].split("#", 1)[1].isdigit()


def test_signup_persiste_username_explicito(client):
    r = client.post(
        "/auth/signup",
        json={
            "email": "b@x.com",
            "password": "12345678",
            "name": "Bruno",
            "username": "brunocoder",
        },
    )
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "brunocoder"


def test_signup_username_em_uso_retorna_409(client, monkeypatch):
    """Par de erro: username já em uso no signup → 409 (não 400 nem 200)."""
    import backend.rbac.auth as auth_mod

    # Libera o 2º signup sem convite (o gate de convite é do handler; aqui só
    # queremos exercer o mapeamento UsernameTakenError → 409).
    monkeypatch.setattr(auth_mod, "has_users", AsyncMock(return_value=False))

    r = client.post(
        "/auth/signup",
        json={
            "email": "a@x.com",
            "password": "12345678",
            "name": "A",
            "username": "dev",
        },
    )
    assert r.status_code == 200

    r2 = client.post(
        "/auth/signup",
        json={
            "email": "b@x.com",
            "password": "12345678",
            "name": "B",
            "username": "dev",
        },
    )
    assert r2.status_code == 409
