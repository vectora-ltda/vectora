"""Testes HTTP dos endpoints de memória.

Usa FastAPI TestClient com um ``NativeInMemoryStore`` real (mesmo Protocol
``StoreBackend`` usado em produção via
``backend.services.agent_factory.get_store``), cobrindo:
- GET /memory — lista vazia, paginação
- POST /memory — cria, 409 duplicado
- PUT /memory/{key} — edita, 404 inexistente
- DELETE /memory/{key} — deleta, 404 inexistente
- DELETE /memory — limpa tudo
- Regressão: API e memory tools do agente leem/escrevem o mesmo namespace
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from backend.testing.mocks import NativeInMemoryStore


@pytest.fixture
def store():
    """``StoreBackend`` real (``NativeInMemoryStore``) compartilhado entre o
    TestClient e as memory tools do agente — instância única por teste
    (mesmo fixture node)."""
    return NativeInMemoryStore()


@pytest.fixture
def client(monkeypatch, store):
    """TestClient com o `store` acima injetado no lugar do store real."""
    os.environ["VECTORA_AUTH_REQUIRED"] = "false"

    async def _fake_get_store():
        """Executa a operação  fake get store."""
        return store

    monkeypatch.setattr("backend.services.agent_factory.get_store", _fake_get_store)

    from backend.api.server import create_app

    app = create_app(serve_static=False)
    tc = TestClient(app)
    yield tc

    os.environ.pop("VECTORA_AUTH_REQUIRED", None)


class TestMemoryList:
    def test_list_empty(self, client) -> None:
        """GET /memory retorna lista vazia inicialmente."""
        resp = client.get("/memory")
        assert resp.status_code == 200
        data = resp.json()
        assert data["memories"] == []
        assert data["total"] == 0

    def test_list_with_items(self, client) -> None:
        """Lista retorna itens após POST."""
        client.post(
            "/memory",
            json={"key": "test1", "content": "conteúdo 1"},
        )
        resp = client.get("/memory")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["memories"]) >= 1
        assert any(m["key"] == "test1" for m in data["memories"])

    def test_list_hides_expired_items(self, client, store) -> None:
        """Verifica o comportamento de list hides expired items."""
        import asyncio

        asyncio.run(
            store.aput(
                ("user", "local", "memories"),
                "old",
                {"content": "expired", "valid_until": "2000-01-01T00:00:00"},
            )
        )
        resp = client.get("/memory")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_get_expired_item_returns_not_found(self, client, store) -> None:
        """Verifica o comportamento de get expired item returns not found."""
        import asyncio

        asyncio.run(
            store.aput(
                ("user", "local", "memories"),
                "old",
                {"content": "expired", "valid_until": "2000-01-01T00:00:00"},
            )
        )
        assert client.get("/memory/old").status_code == 404

    def test_journey_hides_expired_fact(self, client, store) -> None:
        """Verifica o comportamento de journey hides expired fact."""
        import asyncio

        asyncio.run(
            store.aput(
                ("user", "local", "memories"),
                "old-fact",
                {
                    "content": "expired",
                    "valid_until": "2000-01-01T00:00:00",
                    "metadata": {"tag": "user_model"},
                },
            )
        )
        asyncio.run(
            store.aput(
                ("user", "local", "memories"),
                "live-fact",
                {
                    "content": "vigente",
                    "metadata": {"tag": "user_model", "source": "test"},
                },
            )
        )
        response = client.get("/memory/journey")
        assert response.status_code == 200
        assert [fact["key"] for fact in response.json()["facts"]] == ["live-fact"]


class TestMemoryCreate:
    def test_create_happy(self, client) -> None:
        """POST /memory cria nova memória."""
        resp = client.post(
            "/memory",
            json={
                "key": "my_key",
                "content": "my content",
                "metadata": {"tag": "test"},
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "created"
        assert data["key"] == "my_key"

        get_resp = client.get("/memory/my_key")
        assert get_resp.status_code == 200
        mem = get_resp.json()
        assert mem["content"] == "my content"

    def test_create_duplicate_409(self, client) -> None:
        """POST duplicada retorna 409 Conflict."""
        client.post("/memory", json={"key": "dup_key", "content": "first"})
        resp = client.post("/memory", json={"key": "dup_key", "content": "second"})
        assert resp.status_code == 409


class TestMemoryUpdate:
    def test_update_happy(self, client) -> None:
        """PUT /memory/{key} edita conteúdo."""
        client.post("/memory", json={"key": "edit_key", "content": "original"})

        resp = client.put(
            "/memory/edit_key",
            json={"content": "updated"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"

        get_resp = client.get("/memory/edit_key")
        assert get_resp.json()["content"] == "updated"

    def test_update_nonexistent_404(self, client) -> None:
        """PUT em chave inexistente retorna 404."""
        resp = client.put("/memory/nonexistent", json={"content": "should fail"})
        assert resp.status_code == 404


class TestMemoryDelete:
    def test_delete_happy(self, client) -> None:
        """DELETE /memory/{key} remove."""
        client.post("/memory", json={"key": "del_key", "content": "to delete"})
        resp = client.delete("/memory/del_key")
        assert resp.status_code == 200
        assert resp.json()["status"] == "deleted"

        get_resp = client.get("/memory/del_key")
        assert get_resp.status_code == 404

    def test_delete_nonexistent_404(self, client) -> None:
        """DELETE inexistente retorna 404."""
        resp = client.delete("/memory/nonexistent")
        assert resp.status_code == 404


class TestMemorySharedStoreWithAgent:
    """Regressão: painel de configurações e memory tools do agente devem
    compartilhar o mesmo store/namespace — antes, o handler HTTP escrevia
    num MemoryStore SQLite à parte, nunca visto pelo agente (e vice-versa).

    ``backend.tools.memory._get_store`` normalmente resolve o store injetado
    no motor de execução em produção — aqui simulamos essa resolução
    apontando pro mesmo ``InMemoryStore`` do fixture ``client``, que é
    exatamente a instância que a infraestrutura do agente recebe em produção
    (ver ``backend.services.agent_factory._ensure_infra``).
    """

    @pytest.mark.asyncio
    async def test_memory_saved_via_agent_tool_appears_in_api(
        self, client, store, monkeypatch
    ):
        """Verifica o comportamento de memory saved via agent tool appears in api."""
        from backend.tools.context import ToolContext
        from backend.tools.memory import save_memory

        monkeypatch.setattr("backend.tools.memory._get_store", lambda ctx: store)

        await save_memory(
            ctx=ToolContext(user_id="local"),
            key="from_agent",
            content="salvo pelo agente",
        )

        resp = client.get("/memory/from_agent")
        assert resp.status_code == 200
        assert resp.json()["content"] == "salvo pelo agente"

    @pytest.mark.asyncio
    async def test_memory_created_via_api_is_visible_to_agent_tool(
        self, client, store, monkeypatch
    ):
        """Verifica o comportamento de memory created via api is visible to agent tool."""
        from backend.tools.context import ToolContext
        from backend.tools.memory import get_memory

        monkeypatch.setattr("backend.tools.memory._get_store", lambda ctx: store)

        client.post("/memory", json={"key": "from_api", "content": "salvo pelo painel"})

        out = await get_memory(ctx=ToolContext(user_id="local"), key="from_api")
        assert '"status": "found"' in out
        assert "salvo pelo painel" in out


class TestJourney:
    """GET /memory/journey — o que o Remember aprendeu sobre o usuário."""

    def test_usuario_sem_nada_aprendido_retorna_listas_vazias(
        self, client, monkeypatch
    ):
        """Verifica o comportamento de usuario sem nada aprendido retorna listas vazias."""
        monkeypatch.setattr("backend.workspace.skills.list_skills", lambda _u: [])

        resp = client.get("/memory/journey")

        # Edge: nada aprendido ainda é estado normal, não erro — o painel
        # precisa poder renderizar o estado vazio.
        assert resp.status_code == 200
        assert resp.json() == {"facts": [], "skills": []}

    def test_lista_fatos_user_model_e_skills_do_learning_loop(
        self, client, monkeypatch
    ):
        """Verifica o comportamento de lista fatos user model e skills do learning loop."""
        from backend.vtypes.skill import Skill

        client.post(
            "/memory",
            json={
                "key": "learned-fact-1",
                "content": "prefere respostas curtas",
                "metadata": {"tag": "user_model", "source": "learn_from_session"},
            },
        )
        # Erro/borda: memória comum (sem a tag) NÃO pode vazar pro painel —
        # senão "o que aprendi sobre você" viraria um dump de tudo.
        client.post(
            "/memory",
            json={"key": "nota-avulsa", "content": "rodar testes antes do commit"},
        )
        monkeypatch.setattr(
            "backend.workspace.skills.list_skills",
            lambda _u: [
                Skill(
                    id="revisar-pr",
                    name="Revisar PR",
                    description="Como revisar",
                    source="learning-loop",
                    path="/tmp/revisar-pr",
                    installed_at="2026-07-30T10:00:00+00:00",
                    installed_by="local",
                ),
                # Skill instalada manualmente pelo usuário não é algo que o
                # agente "aprendeu" — fica de fora.
                Skill(
                    id="manual",
                    name="Manual",
                    description="Instalada à mão",
                    source="https://github.com/x/y",
                    path="/tmp/manual",
                    installed_at="2026-07-29T10:00:00+00:00",
                    installed_by="local",
                ),
            ],
        )

        data = client.get("/memory/journey").json()

        assert [f["key"] for f in data["facts"]] == ["learned-fact-1"]
        assert data["facts"][0]["source"] == "learn_from_session"
        assert [s["id"] for s in data["skills"]] == ["revisar-pr"]

    def test_falha_ao_listar_skills_nao_derruba_o_painel(
        self, client, monkeypatch
    ) -> None:
        """Verifica o comportamento de falha ao listar skills nao derruba o painel."""

        def _explode(_u):
            """Executa a operação  explode."""
            raise OSError("índice de skills corrompido")

        monkeypatch.setattr("backend.workspace.skills.list_skills", _explode)
        client.post(
            "/memory",
            json={
                "key": "learned-fact-2",
                "content": "usa Windows",
                "metadata": {"tag": "user_model"},
            },
        )

        resp = client.get("/memory/journey")

        # Degrada em vez de 500: os fatos ainda chegam, só as skills somem.
        assert resp.status_code == 200
        assert [f["key"] for f in resp.json()["facts"]] == ["learned-fact-2"]
        assert resp.json()["skills"] == []

    def test_journey_nao_e_capturado_pela_rota_de_chave(
        self, client, monkeypatch
    ) -> None:
        """Regressão de ordem de rota: se `/{key}` for declarada antes,
        "journey" vira uma busca por chave e devolve 404."""
        monkeypatch.setattr("backend.workspace.skills.list_skills", lambda _u: [])

        assert client.get("/memory/journey").status_code == 200
        # Erro/borda: uma chave que realmente não existe segue dando 404.
        assert client.get("/memory/chave-inexistente").status_code == 404
