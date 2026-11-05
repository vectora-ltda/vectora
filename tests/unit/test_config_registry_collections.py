"""Testes do contrato de coleção do registry — `CollectionSettingField` e o
`SqliteTableAdapter` que envolve as tabelas SQLite ad hoc de modelos
registrados por gateway (provider_routing). Isolado do disco real: usa DB em
tmp_path.

Cobertura: contrato de coleção no registry (registra/recupera/listar por
categoria/duplicado) e o adapter de tabela de modelos (list/add/remove +
par de erro).
"""

from __future__ import annotations

import pytest

from backend.config import collections as collections_mod
from backend.config import registry as registry_mod
from backend.config.adapters import RegisteredModelsTableAdapter
from backend.config.collections import (
    CollectionSettingField,
    DuplicateCollectionFieldError,
)


@pytest.fixture  # type: ignore[misc]
def clean_registry():
    """Isola os registries globais (escalar e coleção) entre testes."""
    scalar = dict(registry_mod._REGISTRY)
    collection = dict(collections_mod._COLLECTION_REGISTRY)
    registry_mod._REGISTRY.clear()
    collections_mod._COLLECTION_REGISTRY.clear()
    yield
    registry_mod._REGISTRY.clear()
    collections_mod._COLLECTION_REGISTRY.clear()
    registry_mod._REGISTRY.update(scalar)
    collections_mod._COLLECTION_REGISTRY.update(collection)


# ---------------------------------------------------------------------------
# Contrato de coleção no registry
# ---------------------------------------------------------------------------


class TestCollectionRegistry:
    def test_registra_e_recupera(self, clean_registry):
        from backend.config.collections import collection_field

        collection_field(
            "openrouter_registered_models",
            category="provider_routing",
            description="Modelos registrados do gateway OpenRouter.",
            adapter=RegisteredModelsTableAdapter("openrouter_registered_models"),
        )
        field = collections_mod.get_collection_field("openrouter_registered_models")
        assert field is not None
        assert field.category == "provider_routing"
        assert isinstance(field, CollectionSettingField)

    def test_chave_duplicada_levanta_erro(self, clean_registry):
        from backend.config.collections import collection_field

        collection_field(
            "ollama_registered_models",
            category="provider_routing",
            description="d",
            adapter=RegisteredModelsTableAdapter("ollama_registered_models"),
        )
        with pytest.raises(DuplicateCollectionFieldError):
            collection_field(
                "ollama_registered_models",
                category="provider_routing",
                description="d2",
                adapter=RegisteredModelsTableAdapter("ollama_registered_models"),
            )

    def test_lista_por_categoria(self, clean_registry):
        from backend.config.collections import collection_field

        collection_field(
            "ollama_registered_models",
            category="provider_routing",
            description="d",
            adapter=RegisteredModelsTableAdapter("ollama_registered_models"),
        )
        collection_field(
            "openrouter_registered_models",
            category="provider_routing",
            description="d",
            adapter=RegisteredModelsTableAdapter("openrouter_registered_models"),
        )
        names = [
            f.key for f in collections_mod.collections_for_category("provider_routing")
        ]
        assert sorted(names) == [
            "ollama_registered_models",
            "openrouter_registered_models",
        ]


# ---------------------------------------------------------------------------
# RegisteredModelsTableAdapter — coleção de modelos registrados
# ---------------------------------------------------------------------------


class TestRegisteredModelsTableAdapter:
    @pytest.mark.asyncio
    async def test_add_list_remove(self, tmp_path, monkeypatch):
        # Usa DB isolado em tmp_path via monkeypatch do resolutor de DB.
        import sqlite3

        import aiosqlite

        from backend.api.handlers.provider_routing import _get_db

        db_path = tmp_path / "provider.db"

        async def _fake_get_db():
            return await aiosqlite.connect(db_path)

        monkeypatch.setattr(
            "backend.api.handlers.provider_routing._get_db", _fake_get_db
        )

        adapter = RegisteredModelsTableAdapter("ollama_registered_models")
        model = await adapter.add({"tag": "llama3:8b"})
        assert model["tag"] == "llama3:8b"
        assert model["id"]

        items = await adapter.list_items()
        assert len(items) == 1
        assert items[0]["tag"] == "llama3:8b"

        await adapter.remove(model["id"])
        assert await adapter.list_items() == []

    @pytest.mark.asyncio
    async def test_add_tag_duplicada_levanta_erro(self, tmp_path, monkeypatch):
        import aiosqlite

        db_path = tmp_path / "provider.db"

        async def _fake_get_db():
            return await aiosqlite.connect(db_path)

        monkeypatch.setattr(
            "backend.api.handlers.provider_routing._get_db", _fake_get_db
        )

        adapter = RegisteredModelsTableAdapter("ollama_registered_models")
        await adapter.add({"tag": "llama3:8b"})
        with pytest.raises(Exception) as exc_info:
            await adapter.add({"tag": "llama3:8b"})
        assert "409" in str(exc_info.value) or "209" in str(exc_info.value)


# ---------------------------------------------------------------------------
# UserRowAdapter — env overrides por usuário (users.env_overrides_json)
# ---------------------------------------------------------------------------


class TestUserRowAdapter:
    """Adaptador per-usuário sobre `env_overrides_json`. Não é um
    `SettingField` escalar global (que não tem contexto de usuário) — é o
    wrapper que o handler de env overrides delega, isolando o acesso à
    tabela/por-usuário do resto da lógica de requisição.
    """

    @pytest.mark.asyncio
    async def test_get_set_delete_por_usuario(self, monkeypatch):
        import backend.rbac.auth as auth_svc
        from backend.config.adapters import UserRowAdapter

        store: dict[str, dict[str, str]] = {}

        async def fake_get(user_id: str) -> dict[str, str]:
            return store.get(user_id, {})

        async def fake_set(user_id: str, key: str, value: str) -> None:
            store.setdefault(user_id, {})[key] = value

        async def fake_delete(user_id: str, key: str) -> None:
            store.get(user_id, {}).pop(key, None)

        monkeypatch.setattr(auth_svc, "get_env_overrides", fake_get)
        monkeypatch.setattr(auth_svc, "set_env_override", fake_set)
        monkeypatch.setattr(auth_svc, "delete_env_override", fake_delete)

        adapter = UserRowAdapter("COHERE_API_KEY")
        assert await adapter.get("u1") is None
        await adapter.set("u1", "secret")
        assert await adapter.get("u1") == "secret"
        # Isolamento por usuário: o valor de u1 não vaza para u2.
        assert await adapter.get("u2") is None
        await adapter.delete("u1")
        assert await adapter.get("u1") is None

    @pytest.mark.asyncio
    async def test_user_local_delega_ao_rbac_auth(self, monkeypatch):
        import backend.rbac.auth as auth_svc
        from backend.config.adapters import UserRowAdapter

        async def fake_local_get(user_id: str) -> dict[str, str]:
            return {"COHERE_API_KEY": "local-val"}

        monkeypatch.setattr(auth_svc, "get_env_overrides", fake_local_get)

        adapter = UserRowAdapter("COHERE_API_KEY")
        assert await adapter.get("u1") == "local-val"
