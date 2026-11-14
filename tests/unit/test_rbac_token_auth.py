"""Testes de `backend/rbac/token_auth.py` — tokens de serviço
(máquina-a-máquina).

Cobre create/verify/revoke/list + `has_scope`, cada caminho feliz com o
par de erro/borda no mesmo teste (CLAUDE.md §18): token inválido/revogado
nunca autentica, revogar duas vezes é idempotente sem erro, escopo
ausente é negado.
"""

from __future__ import annotations

import pytest

from backend.rbac import token_auth


@pytest.fixture
async def db(tmp_path):
    import aiosqlite

    conn = await aiosqlite.connect(str(tmp_path / "test_token_auth.db"))
    conn.row_factory = aiosqlite.Row
    await conn.executescript("""
        CREATE TABLE service_tokens (
            id          TEXT    PRIMARY KEY,
            name        TEXT    NOT NULL,
            token_hash  TEXT    NOT NULL UNIQUE,
            scopes_json TEXT    NOT NULL DEFAULT '[]',
            created_by  TEXT,
            created_at  TEXT    NOT NULL,
            revoked_at  TEXT
        );
    """)
    await conn.commit()
    try:
        yield conn
    finally:
        await conn.close()


class TestCreateAndVerify:
    @pytest.mark.asyncio
    async def test_token_criado_verifica_com_sucesso(self, db):
        token_obj, raw_token = await token_auth.create_service_token(
            db, "ci-bot", ["webhook:trigger"]
        )

        assert raw_token.startswith("vst_")
        assert token_obj.name == "ci-bot"
        assert token_obj.scopes == ["webhook:trigger"]

        verificado = await token_auth.verify_service_token(db, raw_token)
        assert verificado is not None
        assert verificado.id == token_obj.id

    @pytest.mark.asyncio
    async def test_token_invalido_ou_ausente_nunca_autentica_sem_lancar(self, db):
        """Erro/borda: token desconhecido, vazio, e um token real revogado
        — os três devolvem None, nunca lançam."""
        assert await token_auth.verify_service_token(db, "vst_nao_existe") is None
        assert await token_auth.verify_service_token(db, "") is None

        token_obj, raw_token = await token_auth.create_service_token(db, "x", [])
        await token_auth.revoke_service_token(db, token_obj.id)
        assert await token_auth.verify_service_token(db, raw_token) is None


class TestRevoke:
    @pytest.mark.asyncio
    async def test_revoga_e_segunda_chamada_e_idempotente(self, db):
        token_obj, _ = await token_auth.create_service_token(db, "x", [])

        assert await token_auth.revoke_service_token(db, token_obj.id) is True
        # Erro/borda: revogar de novo não é erro, só não revoga nada novo.
        assert await token_auth.revoke_service_token(db, token_obj.id) is False

    @pytest.mark.asyncio
    async def test_revogar_id_inexistente_retorna_false_sem_lancar(self, db):
        assert await token_auth.revoke_service_token(db, "id-inexistente") is False


class TestListServiceTokens:
    @pytest.mark.asyncio
    async def test_lista_inclui_revogados_sem_token_cru(self, db):
        t1, _ = await token_auth.create_service_token(db, "ativo", [])
        t2, _ = await token_auth.create_service_token(db, "revogado", [])
        await token_auth.revoke_service_token(db, t2.id)

        tokens = await token_auth.list_service_tokens(db)

        ids = {t.id for t in tokens}
        assert {t1.id, t2.id} <= ids
        revogado = next(t for t in tokens if t.id == t2.id)
        assert revogado.revoked_at is not None
        for t in tokens:
            assert not hasattr(t, "token_hash")

    @pytest.mark.asyncio
    async def test_banco_vazio_retorna_lista_vazia_sem_erro(self, db):
        assert await token_auth.list_service_tokens(db) == []


class TestHasScope:
    def test_escopo_explicito_e_coringa(self):
        t = token_auth.ServiceToken(
            id="1", name="x", scopes=["webhook:trigger"], created_at="now"
        )
        assert token_auth.has_scope(t, "webhook:trigger") is True

        admin = token_auth.ServiceToken(
            id="2", name="x", scopes=["*"], created_at="now"
        )
        assert token_auth.has_scope(admin, "qualquer:coisa") is True

    def test_escopo_ausente_e_negado(self):
        """Erro/borda: token sem o escopo pedido (nem coringa) é negado."""
        t = token_auth.ServiceToken(id="1", name="x", scopes=["read"], created_at="now")
        assert token_auth.has_scope(t, "write") is False
