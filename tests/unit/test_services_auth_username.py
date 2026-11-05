"""Identidade por username — username é coluna persistida e única.

Contrato: username persiste, é único, colisão vira ``base#NNNN``, e
signin/refresh/get_user_by_id carregam o valor gravado.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    """Cada teste usa um SQLite temporário isolado (espelha test_services_auth)."""
    db_file = str(tmp_path / "test_auth.db")
    import backend.rbac.auth as auth_mod

    monkeypatch.setattr(auth_mod, "_db_conn", None)
    monkeypatch.setattr(
        auth_mod, "_get_secret", lambda: "test-secret-key-for-unit-tests-xx"
    )

    import aiosqlite

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
    yield

    async def _close():
        if auth_mod._db_conn is not None:
            await auth_mod._db_conn.close()
            auth_mod._db_conn = None

    asyncio.run(_close())


def _signup(**kw):
    from backend.rbac import auth as auth_svc

    return asyncio.run(auth_svc.signup(**kw))


def test_signup_deriva_username_do_nome():
    user, _, _ = _signup(email="a@x.com", password="12345678", name="Bruno Soares")
    assert user.username == "brunosoares"


def test_segundo_mesmo_nome_recebe_sufixo_de_colisao():
    _signup(email="a@x.com", password="12345678", name="Bruno")
    user2, _, _ = _signup(email="b@x.com", password="12345678", name="Bruno")
    assert user2.username != "bruno"
    # Formato de colisão: bruno#NNNN (4 dígitos).
    assert user2.username.startswith("bruno#")
    suffix = user2.username.split("#", 1)[1]
    assert len(suffix) == 4 and suffix.isdigit()


def test_signup_com_username_explicito_persiste():
    user, _, _ = _signup(
        email="a@x.com", password="12345678", name="Bruno", username="brunocoder"
    )
    assert user.username == "brunocoder"


def test_signup_username_explicito_ja_em_uso_falha():
    from backend.rbac.auth import UsernameTakenError

    _signup(email="a@x.com", password="12345678", name="Bruno", username="dev")
    # Par de erro: o mesmo username explícito por outra conta deve falhar de
    # forma observável (não silenciosamente criar duplicado).
    with pytest.raises(UsernameTakenError):
        _signup(email="b@x.com", password="12345678", name="Outro", username="dev")


def test_username_taken_reflete_estado_do_banco():
    from backend.rbac import auth as auth_svc

    assert asyncio.run(auth_svc.username_taken("bruno")) is False
    _signup(email="a@x.com", password="12345678", name="Bruno")
    assert asyncio.run(auth_svc.username_taken("bruno")) is True
    # Normaliza a entrada: "Bruno" (com maiúscula) é o mesmo username "bruno".
    assert asyncio.run(auth_svc.username_taken("Bruno")) is True


def test_suggest_username_devolve_variacao_livre():
    from backend.rbac import auth as auth_svc

    assert asyncio.run(auth_svc.suggest_username("Bruno")) == "bruno"
    _signup(email="a@x.com", password="12345678", name="Bruno")
    sug = asyncio.run(auth_svc.suggest_username("Bruno"))
    assert sug.startswith("bruno#") and sug.split("#", 1)[1].isdigit()


def test_signin_carrega_username_gravado():
    from backend.rbac import auth as auth_svc

    _signup(email="a@x.com", password="12345678", name="Bruno Soares")
    user, _, _ = asyncio.run(auth_svc.signin("a@x.com", "12345678"))
    assert user.username == "brunosoares"


def test_get_user_by_id_carrega_username_gravado():
    from backend.rbac import auth as auth_svc

    created, _, _ = _signup(email="a@x.com", password="12345678", name="Bruno Soares")
    fetched = asyncio.run(auth_svc.get_user_by_id(created.id))
    assert fetched is not None
    assert fetched.username == "brunosoares"


def test_backfill_gera_username_para_row_legada():
    """Row inserida sem username (schema legado) ganha um no _ensure_schema."""
    from backend.rbac import auth as auth_svc

    async def _scenario():
        db = await auth_svc._get_db()
        # Simula row legada: escreve direto sem username e zera a coluna.
        await db.execute(
            "INSERT INTO users (id, email, password_hash, role, name, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("legacy-1", "old@x.com", "x", "member", "Maria Antiga", "2020-01-01"),
        )
        await db.execute("UPDATE users SET username = '' WHERE id = 'legacy-1'")
        await db.commit()
        # Re-roda o schema (idempotente) — deve backfillar a row vazia.
        await auth_svc._ensure_schema(db)
        return await auth_svc.get_user_by_id("legacy-1")

    user = asyncio.run(_scenario())
    assert user is not None
    assert user.username == "mariaantiga"
