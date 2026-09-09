"""Testes unitários para src/api/server.py e handlers.

Valida:
- Criação da FastAPI app em modo headless
- Rota /health responde OK
- Rota /metrics responde lista
- Rotas dos handlers estão registradas
- GetTools retorna lista (sem erros de importação)
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def headless_app():
    """App FastAPI em modo headless (sem static files).

    Auth é desabilitada via env var para que os testes unitários não
    dependam de um banco de dados de usuários real.
    """
    os.environ["VECTORA_AUTH_REQUIRED"] = "false"
    from backend.api.server import create_app

    return create_app()


@pytest.fixture(scope="module")
def client(headless_app):
    return TestClient(headless_app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


class TestLifespan:
    """``_lifespan`` inicia o BackgroundEmbeddingWorker no startup (além de
    pará-lo no shutdown), para que chunks enfileirados por
    ``ingest_docs``/``ingest_directory`` sejam processados pelo RAG.
    """

    def test_lifespan_starts_embedding_worker(self, headless_app, monkeypatch):
        import backend.embedding.background as bg

        started = {"value": False}

        class _FakeWorker:
            async def start(self) -> None:
                started["value"] = True

            async def stop(self, *_a, **_k) -> None:
                pass

        async def _fake_get_worker() -> _FakeWorker:
            return _FakeWorker()

        monkeypatch.setattr(bg, "get_background_worker", _fake_get_worker)

        # Entrar/sair do TestClient como context manager dispara o lifespan.
        with TestClient(headless_app, raise_server_exceptions=False):
            pass

        assert started["value"], (
            "_lifespan não iniciou o BackgroundEmbeddingWorker — chunks "
            "enfileirados ficariam pending e o RAG não recuperaria nada."
        )

    def test_lifespan_runs_thread_cleanup_immediately_at_boot(
        self, headless_app, monkeypatch
    ):
        """`cleanup_empty_threads` roda imediatamente no boot, sem esperar
        o primeiro `asyncio.sleep` do loop de limpeza — threads vazias de
        sessões anteriores não ficam visíveis até essa espera. Também
        confirma a ORDEM: reconcile_vectora_sessions sempre roda antes de
        cleanup_empty_threads (uma thread real com message_count zerado por
        alguma falha não pode ser apagada antes da reconciliação ter a
        chance de corrigir a contagem — ver server.py)."""
        import backend.api.handlers.threads as threads_handler

        call_count = {"value": 0}
        events: list[str] = []

        async def _fake_cleanup(*_a, **_k) -> int:
            call_count["value"] += 1
            events.append("cleanup")
            return 0

        async def _fake_reconcile() -> int:
            # Mockada só pra não competir por tempo com cleanup_empty_threads
            # nesse teste — reconcile_vectora_sessions roda ANTES dela no
            # loop real (ver server.py) e faz I/O de verdade (SessionStore),
            # o que atrasaria o assert abaixo além da janela do TestClient.
            events.append("reconcile")
            return 0

        monkeypatch.setattr(threads_handler, "cleanup_empty_threads", _fake_cleanup)
        monkeypatch.setattr(
            threads_handler, "reconcile_vectora_sessions", _fake_reconcile
        )

        with TestClient(headless_app, raise_server_exceptions=False):
            pass

        assert events[:2] == ["reconcile", "cleanup"], (
            "reconcile_vectora_sessions precisa rodar ANTES de "
            "cleanup_empty_threads no boot — ordem real observada: "
            f"{events!r}"
        )
        assert call_count["value"] >= 1, (
            "cleanup_empty_threads não rodou no boot — o loop só chama "
            "após o primeiro asyncio.sleep(3600), deixando threads "
            "fantasma visíveis por até 1h a cada restart."
        )


class TestHealth:
    def test_health_ok(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert "version" in body

    def test_health_version_non_empty(self, client):
        response = client.get("/health")
        assert response.json()["version"] != ""


class TestUpdateChangelog:
    def test_returns_latest_packaged_release_notes(self, client):
        response = client.get("/api/updates/changelog")
        assert response.status_code == 200
        body = response.json()
        assert body["version"]
        assert body["notes"].startswith("## ")


# ---------------------------------------------------------------------------
# /metrics
# ---------------------------------------------------------------------------


class TestMetrics:
    def test_metrics_returns_list(self, client):
        response = client.get("/metrics")
        assert response.status_code == 200
        assert isinstance(response.json(), list)


# ---------------------------------------------------------------------------
# Rotas registradas
# ---------------------------------------------------------------------------


def _collect_route_paths(routes) -> list[str]:
    """Coleta paths recursivamente — FastAPI 0.138 usa _IncludedRouter sem .path."""
    paths: list[str] = []
    for r in routes:
        if hasattr(r, "path") and r.path is not None:
            paths.append(r.path)
        orig = getattr(r, "original_router", None)
        if orig is not None and hasattr(orig, "routes"):
            paths.extend(_collect_route_paths(orig.routes))
        elif hasattr(r, "routes") and r.routes:
            paths.extend(_collect_route_paths(r.routes))
    return paths


class TestRoutes:
    def _route_paths(self, app) -> list[str]:
        return _collect_route_paths(app.routes)

    def test_stream_chat_route_exists(self, headless_app):
        paths = self._route_paths(headless_app)
        assert "/vectora.chat.v1.ChatService/StreamChat" in paths

    def test_resume_chat_route_exists(self, headless_app):
        paths = self._route_paths(headless_app)
        assert "/vectora.chat.v1.ChatService/ResumeChat" in paths

    def test_get_tools_route_exists(self, headless_app):
        paths = self._route_paths(headless_app)
        assert "/vectora.chat.v1.ChatService/GetTools" in paths

    def test_workspaces_active_route_exists(self, headless_app):
        paths = self._route_paths(headless_app)
        assert "/workspaces/active" in paths, (
            "GET /workspaces/active ausente — GitStatusBadge chama esse "
            "endpoint a cada 5 s; sem a rota o proxy encaminha para o Vite "
            "e falha com 502."
        )

    def test_thread_routes_exist(self, headless_app):
        paths = self._route_paths(headless_app)
        for route in (
            "/vectora.chat.v1.ThreadService/CreateThread",
            "/vectora.chat.v1.ThreadService/GetThread",
            "/vectora.chat.v1.ThreadService/ListThreads",
            "/vectora.chat.v1.ThreadService/DeleteThread",
            "/vectora.chat.v1.ThreadService/GetHistory",
        ):
            assert route in paths, f"Rota ausente: {route}"

    def test_share_routes_exist(self, headless_app):
        paths = self._route_paths(headless_app)
        assert "/threads/share" in paths
        assert "/threads/share/{token}" in paths


# ---------------------------------------------------------------------------
# /workspaces/active — alias REST-friendly (sem auth required = false)
# ---------------------------------------------------------------------------


class TestWorkspacesActive:
    def test_returns_200(self, client):
        res = client.get("/workspaces/active")
        assert res.status_code == 200

    def test_response_has_workspace_key(self, client):
        body = client.get("/workspaces/active").json()
        assert "workspace" in body, (
            "Resposta não tem campo 'workspace' — GitStatusBadge acessa "
            "'data.workspace?.git_current_branch'."
        )

    def test_workspace_is_none_or_object(self, client):
        body = client.get("/workspaces/active").json()
        ws = body["workspace"]
        if ws is not None:
            assert "id" in ws
            assert "git_current_branch" in ws or ws.get("is_git_repo") is False


# ---------------------------------------------------------------------------
# API tools schema — rota interna consumida pelo frontend
# ---------------------------------------------------------------------------


class TestApiToolsSchema:
    def test_api_tools_schema_route_exists(self, headless_app):
        paths = _collect_route_paths(headless_app.routes)
        assert "/api/tools/schema" in paths


# ---------------------------------------------------------------------------
# GetTools (endpoint síncrono — pode testar sem graph)
# ---------------------------------------------------------------------------


class TestGetTools:
    def test_get_tools_returns_200(self, client):
        response = client.get("/vectora.chat.v1.ChatService/GetTools")
        assert response.status_code == 200

    def test_get_tools_has_tools_key(self, client):
        response = client.get("/vectora.chat.v1.ChatService/GetTools")
        body = response.json()
        assert "tools" in body
        assert isinstance(body["tools"], list)

    def test_get_tools_each_has_name(self, client):
        response = client.get("/vectora.chat.v1.ChatService/GetTools")
        tools = response.json()["tools"]
        if tools:  # pode ser vazia em ambiente de teste sem ALL_TOOLS
            for t in tools:
                assert "name" in t
                assert "render_hint" in t


# ---------------------------------------------------------------------------
# Modo chat sem static dir não levanta exceção
# ---------------------------------------------------------------------------


class TestChatModeWithoutStaticDir:
    def test_chat_mode_with_frontend_proxy(self):
        """create_app(serve_static=True) registra o proxy do frontend sem explodir."""
        from backend.api.server import create_app

        app = create_app(serve_static=True)
        c = TestClient(app, raise_server_exceptions=False)
        resp = c.get("/health")
        assert resp.status_code == 200
