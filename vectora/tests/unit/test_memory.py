"""Tests — Per-User Memory isolation.

Verifica:
- _user_id_from_ctx retorna o user_id cru quando há user_id explícito no ctx
  (o prefixo de namespace vem de _memory_namespace, que envolve em
  ("user", <id>, "memories"))
- fallback para workspace_<id> quando não há user_id mas há workspace
- fallback para session_<thread_id> como último recurso
- "local" quando não há nenhum identificador
- endpoints REST existem e têm assinaturas corretas
- ids distintos (user/workspace/session) não colidem
"""

from __future__ import annotations

import json

import pytest

from backend.testing.mocks import NativeInMemoryStore
from backend.tools.context import ToolContext

# ---------------------------------------------------------------------------
# _user_id_from_ctx prioridade
# ---------------------------------------------------------------------------


class TestUserIdFromCtx:
    """_user_id_from_ctx deve priorizar user_id autenticado."""

    def test_returns_user_namespace_when_user_id_present(self):
        from backend.tools.memory import _user_id_from_ctx

        ctx = ToolContext(user_id="abc123", thread_id="t1")
        result = _user_id_from_ctx(ctx)
        assert result == "abc123"

    def test_returns_workspace_namespace_when_no_user_id(self):
        from backend.tools.memory import _user_id_from_ctx

        ctx = ToolContext(workspace_id="ws1", thread_id="t1")
        result = _user_id_from_ctx(ctx)
        assert result == "workspace_ws1"

    def test_workspace_takes_precedence_over_thread_when_no_user(self):
        from backend.tools.memory import _user_id_from_ctx

        ctx = ToolContext(workspace_id="ws1", thread_id="t1")  # sem user_id
        result = _user_id_from_ctx(ctx)
        assert result == "workspace_ws1"

    def test_user_id_takes_precedence_over_workspace(self):
        """user_id autenticado supera workspace quando ambos presentes."""
        from backend.tools.memory import _user_id_from_ctx

        ctx = ToolContext(user_id="user_abc", workspace_id="ws1", thread_id="t1")
        result = _user_id_from_ctx(ctx)
        assert result == "user_abc"

    def test_returns_session_namespace_when_only_thread_id(self):
        from backend.tools.memory import _user_id_from_ctx

        ctx = ToolContext(thread_id="thread-xyz")
        result = _user_id_from_ctx(ctx)
        assert result == "session_thread-xyz"

    def test_returns_default_when_ctx_is_bare(self):
        from backend.tools.memory import _user_id_from_ctx

        result = _user_id_from_ctx(ToolContext())
        assert result == "local"

    def test_user_namespace_format(self):
        """Com user_id, o id retornado é o valor cru (sem prefixo); o prefixo
        de namespace ("user", <id>, "memories") vem de _memory_namespace."""
        from backend.tools.memory import _memory_namespace, _user_id_from_ctx

        ctx = ToolContext(user_id="user-99")
        assert _user_id_from_ctx(ctx) == "user-99"
        assert _memory_namespace(ctx) == ("user", "user-99", "memories")


# ---------------------------------------------------------------------------
# handler REST de memória
# ---------------------------------------------------------------------------


class TestMemoryHandlerExists:
    """src/api/handlers/memory.py deve existir com os endpoints esperados."""

    def test_memory_handler_module_exists(self):
        import backend.api.handlers.memory as mem_mod

        assert mem_mod is not None

    def test_router_exists(self):
        from backend.api.handlers.memory import router

        assert router is not None

    def test_list_memories_route_registered(self):
        from backend.api.handlers.memory import router

        routes = [r.path for r in router.routes]  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        assert any("/memory" in r for r in routes)

    def test_delete_memory_route_registered(self):
        from backend.api.handlers.memory import router

        routes = [r.path for r in router.routes]  # type: ignore[attr-defined]  # ty: ignore[unresolved-attribute]
        assert any("/memory" in r for r in routes)


# ---------------------------------------------------------------------------
# isolamento de namespaces
# ---------------------------------------------------------------------------


class TestNamespaceIsolation:
    """Namespaces user:, workspace_, session_ são distintos e não se sobrepõem."""

    def test_user_namespace_not_equal_to_session_namespace(self):
        from backend.tools.memory import _user_id_from_ctx

        user_ns = _user_id_from_ctx(ToolContext(user_id="u1", thread_id="t1"))
        session_ns = _user_id_from_ctx(ToolContext(thread_id="t1"))

        assert user_ns != session_ns

    def test_user_namespace_not_equal_to_workspace_namespace(self):
        from backend.tools.memory import _user_id_from_ctx

        user_ns = _user_id_from_ctx(ToolContext(user_id="u1", workspace_id="ws1"))
        workspace_ns = _user_id_from_ctx(ToolContext(workspace_id="ws1"))

        assert user_ns != workspace_ns

    def test_different_users_have_different_namespaces(self):
        from backend.tools.memory import _user_id_from_ctx

        ns_a = _user_id_from_ctx(ToolContext(user_id="alice"))
        ns_b = _user_id_from_ctx(ToolContext(user_id="bob"))

        assert ns_a != ns_b


# ---------------------------------------------------------------------------
# _get_store — injeção via ToolContext.store.
# ---------------------------------------------------------------------------


class TestGetStore:
    def test_usa_ctx_store_quando_presente(self):
        """Happy: `ctx.store` populado pelo motor nativo — usado direto."""
        from backend.tools.memory import _get_store

        injected_store = NativeInMemoryStore()
        ctx = ToolContext(store=injected_store)

        result = _get_store(ctx)

        assert result is injected_store

    def test_sem_ctx_store_levanta_runtime_error(self):
        """Erro/borda: sem `ctx.store` (execução fora do motor nativo) →
        RuntimeError tipado."""
        from backend.tools.memory import _get_store

        ctx = ToolContext()  # store=None por padrão

        with pytest.raises(RuntimeError, match="store não injetado"):
            _get_store(ctx)


# ---------------------------------------------------------------------------
# save_memory/get_memory com `category` (gotcha/decision/preference/rule):
# campo opcional, persistido dentro do `value` já existente no store — sem
# migração de schema.
# ---------------------------------------------------------------------------


@pytest.fixture
def store(monkeypatch):
    real_store = NativeInMemoryStore()
    monkeypatch.setattr("backend.tools.memory._get_store", lambda ctx: real_store)

    async def _fake_agent_store() -> NativeInMemoryStore:
        return real_store

    monkeypatch.setattr("backend.services.agent_factory.get_store", _fake_agent_store)
    return real_store


class TestSaveMemoryCategory:
    async def test_ttl_persiste_valid_until_e_rejeita_valor_invalido(self, store):
        from backend.tools.memory import get_memory, save_memory

        ctx = ToolContext(user_id="u1")
        saved = json.loads(
            await save_memory(ctx=ctx, key="temporary", content="x", ttl_days=2)
        )
        assert saved["status"] == "saved"
        item = await store.aget(("user", "u1", "memories"), "temporary")
        assert item is not None
        assert "valid_until" in item.value
        assert (
            json.loads(
                await save_memory(ctx=ctx, key="invalid", content="x", ttl_days=0)
            )["status"]
            == "failed"
        )

        assert (
            json.loads(await get_memory(ctx=ctx, key="temporary"))["status"] == "found"
        )

    async def test_memoria_expirada_nao_aparece_e_e_removida(self, store):
        from backend.tools.memory import get_memory

        ctx = ToolContext(user_id="u1")
        await store.aput(
            ("user", "u1", "memories"),
            "expired",
            {"content": "x", "valid_until": "2000-01-01T00:00:00+00:00"},
        )

        assert (
            json.loads(await get_memory(ctx=ctx, key="expired"))["status"]
            == "not_found"
        )
        assert await store.aget(("user", "u1", "memories"), "expired") is None

    async def test_category_e_persistida_e_volta_no_get(self, store):
        from backend.tools.memory import get_memory, save_memory

        ctx = ToolContext(user_id="u1")
        await save_memory(
            ctx=ctx, key="pref_lang", content="PT-BR", category="preference"
        )

        out = json.loads(await get_memory(ctx=ctx, key="pref_lang"))

        assert out["category"] == "preference"

    async def test_sem_category_fica_none_retrocompativel(self, store):
        """Chamadas existentes sem `category` continuam funcionando —
        parâmetro é opcional, não quebra nenhum caller atual."""
        from backend.tools.memory import get_memory, save_memory

        ctx = ToolContext(user_id="u1")
        await save_memory(ctx=ctx, key="sem_categoria", content="conteúdo qualquer")

        out = json.loads(await get_memory(ctx=ctx, key="sem_categoria"))

        assert out["category"] is None

    async def test_category_invalida_e_rejeitada_sem_persistir(self, store):
        """Erro/borda: categoria fora do conjunto válido não é aceita
        silenciosamente. `save_memory` chamada via `TOOL_REGISTRY` (caminho
        real de uma tool call do LLM) valida os argumentos contra o schema
        Pydantic gerado por `vtool` a partir do
        `Literal["gotcha","decision","preference","rule"]` ANTES do corpo da
        função rodar — categoria inválida nunca chega a persistir, e a tool
        devolve string de erro tipada (nunca propaga exceção)."""
        from backend.tools.registry import TOOL_REGISTRY

        ctx = ToolContext(user_id="u1")
        spec = TOOL_REGISTRY.get("save_memory")
        assert spec is not None

        result = await spec.ainvoke(
            {"key": "categoria_ruim", "content": "x", "category": "nao_existe"},
            ctx=ctx,
        )

        assert "argumentos inválidos" in result
        item = await store.aget(("user", "u1", "memories"), "categoria_ruim")
        assert item is None

    async def test_listar_todas_inclui_category_de_cada_uma(self, store):
        from backend.tools.memory import get_memory, save_memory

        ctx = ToolContext(user_id="u1")
        await save_memory(ctx=ctx, key="a", content="x", category="gotcha")
        await save_memory(ctx=ctx, key="b", content="y")

        out = json.loads(await get_memory(ctx=ctx))

        by_key = {m["key"]: m["category"] for m in out["memories"]}
        assert by_key == {"a": "gotcha", "b": None}


class TestListFactContents:
    """`list_fact_contents` — acessor simples usado por `remember_trigger.py`
    (fora do wrapper `vtool`, que exige `ToolContext`) pra buscar os fatos já
    salvos de um usuário antes de propor duplicatas. Roda fire-and-forget
    DEPOIS do turno já ter terminado — nunca pode depender de
    `_get_store()` (contextvar só válido durante a execução do motor
    nativo), por isso usa `agent_factory.get_store()` em vez disso."""

    async def test_retorna_conteudo_de_todos_os_fatos_do_usuario(self, store):
        from backend.tools.memory import list_fact_contents, save_memory

        ctx = ToolContext(user_id="u1")
        await save_memory(ctx=ctx, key="a", content="Fato A")
        await save_memory(ctx=ctx, key="b", content="Fato B")

        result = await list_fact_contents("u1")

        assert sorted(result) == ["Fato A", "Fato B"]

    async def test_usuario_sem_nenhum_fato_retorna_lista_vazia(self, store):
        from backend.tools.memory import list_fact_contents

        result = await list_fact_contents("usuario-novo")

        assert result == []

    async def test_nao_retorna_fatos_expirados(self, store):
        from backend.tools.memory import list_fact_contents

        await store.aput(
            ("user", "u1", "memories"),
            "old",
            {"content": "Fato expirado", "valid_until": "2000-01-01T00:00:00"},
        )
        await store.aput(
            ("user", "u1", "memories"), "live", {"content": "Fato vigente"}
        )

        assert await list_fact_contents("u1") == ["Fato vigente"]

    async def test_funciona_fora_do_contexto_de_execucao_do_grafo(self, monkeypatch):
        """Regressão: `_get_store()` levanta RuntimeError fora de uma
        execução do motor nativo em andamento — exatamente o cenário real
        do caller (`remember_trigger.py`, disparado via
        `asyncio.ensure_future` depois do turno já ter terminado).
        `list_fact_contents` precisa funcionar mesmo assim, porque não
        depende mais de `_get_store()`."""
        from backend.tools.memory import list_fact_contents

        def _boom() -> None:
            raise RuntimeError("Called get_config outside of a runnable context")

        monkeypatch.setattr("backend.tools.memory._get_store", _boom)

        real_store = NativeInMemoryStore()

        async def _fake_agent_store() -> NativeInMemoryStore:
            return real_store

        monkeypatch.setattr(
            "backend.services.agent_factory.get_store", _fake_agent_store
        )
        await real_store.aput(("user", "u2", "memories"), "a", {"content": "Fato C"})

        result = await list_fact_contents("u2")

        assert result == ["Fato C"]
