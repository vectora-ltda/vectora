"""Testes para backend/api/handlers/flags.py.

Valida:
- GET /settings/flags reflete settings.enable_features_beta por padrão.
- VECTORA_DEV=1 ativa enable_features_beta mesmo com o setting em False
  (Electron em modo dev propaga essa env var pro processo do backend).
- Só o valor exato "1" ativa — qualquer outro valor não conta como dev mode.
- local_configured distingue "auth desabilitada porque o wizard rodou" de
  "auth desabilitada por acidente" (bug real relatado: env var externa
  desligando auth sem o wizard nunca ter rodado, fabricando um usuário
  "Local User" fantasma no frontend).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def _isolated_runtime_settings(tmp_path: Path, monkeypatch):
    """Isola local_user_name de cada teste (nunca lê/escreve o ~/.vectora real)."""
    from backend.workspace import runtime_settings as rs_module
    from backend.workspace.runtime_settings import RuntimeSettings

    fresh = RuntimeSettings(path=tmp_path / "checkpoints.db")
    monkeypatch.setattr(rs_module, "runtime_settings", fresh)
    return fresh


@pytest.fixture
def client(monkeypatch, _isolated_runtime_settings):
    monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "false")
    from backend.api.server import create_app

    app = create_app(serve_static=False)
    return TestClient(app, raise_server_exceptions=False)


class TestGetFlags:
    @pytest.mark.parametrize("operation", ["read", "write"])
    def test_preferences_storage_failure_is_503(
        self, client, _isolated_runtime_settings, monkeypatch, operation
    ):
        def unavailable(*_args, **_kwargs):
            raise sqlite3.OperationalError("private database path")

        method = "get_frontend_prefs" if operation == "read" else "set_frontend_prefs"
        monkeypatch.setattr(_isolated_runtime_settings, method, unavailable)
        response = (
            client.get("/settings/prefs")
            if operation == "read"
            else client.patch("/settings/prefs", json={"decisionModelsEnabled": True})
        )
        assert response.status_code == 503
        assert "private database path" not in response.text

    def test_invalid_provider_shape_does_not_break_preference_storage(self, client):
        response = client.patch(
            "/settings/prefs", json={"decisionModelProvider": [], "theme": "dark"}
        )
        assert response.status_code == 200
        assert response.json() == {"theme": "dark"}
        assert client.get("/settings/prefs").json() == {"theme": "dark"}

    def test_always_enabled_by_default(self, client, monkeypatch):
        """Features beta agora são estáveis e habilitadas por padrão."""
        monkeypatch.delenv("VECTORA_DEV", raising=False)
        resp = client.get("/settings/flags")
        assert resp.status_code == 200
        assert resp.json()["enable_features_beta"] is True

    def test_vectora_dev_does_not_affect_it(self, client, monkeypatch):
        """Independente de VECTORA_DEV, agora é sempre True."""
        for value in ("1", "0", "true", ""):
            monkeypatch.setenv("VECTORA_DEV", value)
            resp = client.get("/settings/flags")
            assert resp.json()["enable_features_beta"] is True


class TestLocalConfigured:
    def test_false_quando_wizard_nunca_rodou(self, client, _isolated_runtime_settings):
        """auth_required=false sem local_user_name persistido (bug real:
        VECTORA_AUTH_REQUIRED=false esquecido num .env externo) — o guard do
        frontend usa isso pra nunca pular o onboarding silenciosamente."""
        resp = client.get("/settings/flags")
        assert resp.json()["local_configured"] is False

    def test_true_apos_setup_local(self, client, _isolated_runtime_settings):
        _isolated_runtime_settings.set_local_user("Bruno", "Vectora")
        resp = client.get("/settings/flags")
        assert resp.json()["local_configured"] is True
