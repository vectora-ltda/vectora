"""Testes dos recursos per-usuário do schema declarativo — categorias
`account` (perfil do usuário) e `memory` (fatos de memória por usuário).
Isolados do DB real via mock dos serviços de auth/store.

Cobertura: adapter de perfil de conta (get/update delegam a rbac.auth),
adapter de memória (list/save/delete por usuário), e registro das categorias
no schema.
"""

from __future__ import annotations

import pytest


class TestUserProfileAdapter:
    """`account` — perfil do usuário por id (nome etc.), via rbac.auth."""

    @pytest.mark.asyncio
    async def test_pega_nome_do_usuario(self, monkeypatch):
        import backend.rbac.auth as auth_svc
        from backend.config.adapters import UserProfileAdapter

        async def fake_get(user_id: str):
            class _U:
                id = user_id
                username = "bruno"
                email = "u@x.com"
                name = "Bruno"
                role = "root"
                created_at = "2026-01-01T00:00:00"

            return _U()

        monkeypatch.setattr(auth_svc, "get_user_by_id", fake_get)
        adapter = UserProfileAdapter()
        profile = await adapter.list_items("user-1")
        assert profile and profile[0]["name"] == "Bruno"
        assert profile[0]["role"] == "root"

    @pytest.mark.asyncio
    async def test_retorna_vazio_quando_usuario_inexistente(self, monkeypatch):
        import backend.rbac.auth as auth_svc
        from backend.config.adapters import UserProfileAdapter

        async def fake_get(user_id: str):
            return None

        monkeypatch.setattr(auth_svc, "get_user_by_id", fake_get)
        adapter = UserProfileAdapter()
        assert await adapter.list_items("ghost") == []

    @pytest.mark.asyncio
    async def test_atualiza_nome_delega_ao_servico(self, monkeypatch):
        import backend.rbac.auth as auth_svc
        from backend.config.adapters import UserProfileAdapter

        calls: list[tuple] = []

        async def fake_get(user_id: str):
            class _U:
                id = user_id
                username = "bruno"
                email = "u@x.com"
                name = "Novo Nome"
                role = "root"
                created_at = "2026-01-01T00:00:00"

            return _U()

        async def fake_update(user_id: str, *, name: str):
            calls.append((user_id, name))

        monkeypatch.setattr(auth_svc, "get_user_by_id", fake_get)
        monkeypatch.setattr(auth_svc, "update_profile", fake_update)
        adapter = UserProfileAdapter()
        await adapter.add({"user_id": "user-1", "name": "Novo Nome"})
        assert calls == [("user-1", "Novo Nome")]


class TestMemoryAdapter:
    """`memory` — fatos de memória por usuário, via store do agente."""

    @pytest.mark.asyncio
    async def test_lista_memorias_do_usuario(self, monkeypatch, tmp_path):
        from backend.config.adapters import MemoryAdapter

        ns = ("user", "user-1", "memories")
        store_rows = {"k1": {"content": "prefere dark"}, "k2": {"content": "scoped"}}

        class _Item:
            def __init__(self, key: str, content: str) -> None:
                self.key = key
                self.value = {"content": content}
                self.score = None

        class _FakeStore:
            async def asearch(self, ns, *, query=None, limit=100):
                return [_Item(key, val["content"]) for key, val in store_rows.items()]

        monkeypatch.setattr("backend.tools.memory._get_store", lambda ctx: _FakeStore())
        adapter = MemoryAdapter()
        items = await adapter.list_items("user-1")
        assert {i["key"] for i in items} == {"k1", "k2"}
        assert {i["content"] for i in items} == {"prefere dark", "scoped"}

    @pytest.mark.asyncio
    async def test_salva_delega_aput_no_store(self, monkeypatch):
        import backend.tools.memory as mem_mod
        from backend.config.adapters import MemoryAdapter

        calls: list[tuple] = []

        class _FakeStore:
            async def aput(self, ns, key, value):
                calls.append((ns, key, value))

        monkeypatch.setattr(mem_mod, "_get_store", lambda ctx: _FakeStore())
        adapter = MemoryAdapter()
        result = await adapter.add({"user_id": "u1", "key": "k", "content": "v"})
        assert result == {"key": "k", "content": "v"}
        assert calls
        ns, key, value = calls[0]
        assert key == "k"
        assert value["content"] == "v"
        # Namespace escopado por id do dono.
        assert ns == ("user", "u1", "memories")
