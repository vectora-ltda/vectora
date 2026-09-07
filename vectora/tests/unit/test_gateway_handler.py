"""backend/api/handlers/gateway.py (status/revoke do gateway local)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# GET /gateway/status — sem token salvo
# ---------------------------------------------------------------------------


class TestGatewayStatusSemToken:
    def _app(self) -> TestClient:
        from fastapi import FastAPI

        from backend.api.handlers.gateway import router

        app = FastAPI()

        @app.middleware("http")
        async def inject_user(request, call_next):
            request.state.user = {"id": "u1"}
            return await call_next(request)

        app.include_router(router)
        return TestClient(app)

    def test_retorna_desconectado_sem_token(self, tmp_path: Path) -> None:
        """Erro/borda: nunca conectou é um estado NEUTRO (never_connected),
        distinto de um erro real — a UI não deve tratar como falha."""
        token_path = tmp_path / "gateway_token"
        with patch("backend.api.handlers.gateway._TOKEN_PATH", token_path):
            res = self._app().get("/gateway/status")
        assert res.status_code == 200
        data = res.json()
        assert data["connected"] is False
        assert data["state"] == "never_connected"
        assert data["detail"] is None
        assert set(data) == {"connected", "state", "detail"}

    def test_retorna_401_sem_autenticacao(self, tmp_path: Path) -> None:
        from fastapi import FastAPI

        from backend.api.handlers.gateway import router

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app, raise_server_exceptions=False)
        res = client.get("/gateway/status")
        assert res.status_code == 401


# ---------------------------------------------------------------------------
# GET /gateway/status — com token salvo
# ---------------------------------------------------------------------------


class TestGatewayStatusComToken:
    def _app(self) -> TestClient:
        from fastapi import FastAPI

        from backend.api.handlers.gateway import router

        app = FastAPI()

        @app.middleware("http")
        async def inject_user(request, call_next):
            request.state.user = {"id": "u1"}
            return await call_next(request)

        app.include_router(router)
        return TestClient(app)

    def test_retorna_status_do_worker(self, tmp_path: Path) -> None:
        token_path = tmp_path / "gateway_token"
        token_path.write_text("abc123")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json = AsyncMock(return_value={"connected": True, "queued": 0})
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=mock_ctx)
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("backend.api.handlers.gateway._TOKEN_PATH", token_path):
            with patch(
                "backend.api.handlers.gateway.aiohttp.ClientSession",
                return_value=mock_session_ctx,
            ):
                res = self._app().get("/gateway/status")

        assert res.status_code == 200
        data = res.json()
        assert data["connected"] is True
        assert data["state"] == "connected"
        assert data["detail"] is None
        assert set(data) == {"connected", "state", "detail"}

    def test_retorna_erro_real_se_worker_offline(self, tmp_path: Path) -> None:
        """Erro/borda: já teve token (tentativa real de conexão) mas o
        Worker não respondeu — state='error', distinto de never_connected,
        com detalhe da falha pra UI mostrar algo além de "desconectado"."""
        token_path = tmp_path / "gateway_token"
        token_path.write_text("abc123")

        with patch("backend.api.handlers.gateway._TOKEN_PATH", token_path):
            with patch(
                "backend.api.handlers.gateway.aiohttp.ClientSession",
                side_effect=Exception("network error"),
            ):
                res = self._app().get("/gateway/status")

        assert res.status_code == 200
        data = res.json()
        assert data["connected"] is False
        assert data["state"] == "error"
        assert data["detail"] == "network error"


# ---------------------------------------------------------------------------
# POST /gateway/revoke
# ---------------------------------------------------------------------------


class TestGatewayRevoke:
    def _app(self) -> TestClient:
        from fastapi import FastAPI

        from backend.api.handlers.gateway import router

        app = FastAPI()

        @app.middleware("http")
        async def inject_user(request, call_next):
            request.state.user = {"id": "u1"}
            return await call_next(request)

        app.include_router(router)
        return TestClient(app)

    def test_retorna_404_sem_token(self, tmp_path: Path) -> None:
        token_path = tmp_path / "gateway_token"
        with patch("backend.api.handlers.gateway._TOKEN_PATH", token_path):
            res = self._app().post("/gateway/revoke")
        assert res.status_code == 404

    def test_revoga_e_deleta_arquivo(self, tmp_path: Path) -> None:
        token_path = tmp_path / "gateway_token"
        token_path.write_text("abc123")

        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_ctx = AsyncMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_session = AsyncMock()
        mock_session.delete = MagicMock(return_value=mock_ctx)
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)

        with patch("backend.api.handlers.gateway._TOKEN_PATH", token_path):
            with patch(
                "backend.api.handlers.gateway.aiohttp.ClientSession",
                return_value=mock_session_ctx,
            ):
                res = self._app().post("/gateway/revoke")

        assert res.status_code == 200
        assert res.json()["revoked"] is True
        assert not token_path.exists()

    def test_revoga_mesmo_se_worker_falhar(self, tmp_path: Path) -> None:
        token_path = tmp_path / "gateway_token"
        token_path.write_text("abc123")

        with patch("backend.api.handlers.gateway._TOKEN_PATH", token_path):
            with patch(
                "backend.api.handlers.gateway.aiohttp.ClientSession",
                side_effect=Exception("network error"),
            ):
                res = self._app().post("/gateway/revoke")

        assert res.status_code == 200
        assert not token_path.exists()
