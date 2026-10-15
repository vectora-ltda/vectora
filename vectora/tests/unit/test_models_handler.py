"""Testes para backend/api/handlers/models.py.

Valida:
- GET /models/providers devolve `providers` (lista) e `dynamic_models` (vazia
  por padrão).
- Modelo Ollama registrado via /provider-routing/ollama/registered aparece em
  `dynamic_models` como "ollama:<tag>".
- Modelo OpenRouter registrado via /provider-routing/openrouter/registered aparece em
  `dynamic_models` como "openrouter:<tag>", junto (não em vez) do Ollama.
- `tool_incompatible_models` expõe o catálogo estático de modelos que
  rejeitam replay de tool_calls (usado pelo frontend pra filtrar o seletor
  no code mode).
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    os.environ["VECTORA_AUTH_REQUIRED"] = "false"
    from backend.api.server import create_app

    app = create_app(serve_static=False)
    return TestClient(app, raise_server_exceptions=False)


class TestModelsProviders:
    def test_returns_providers_and_empty_dynamic_models(self, client):
        resp = client.get("/models/providers")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["providers"], list)
        assert isinstance(body["dynamic_models"], list)

    def test_tool_incompatible_models_includes_cohere_command_a_plus(self, client):
        from backend.settings import TOOL_CALLING_INCOMPATIBLE_MODELS

        resp = client.get("/models/providers")
        assert resp.status_code == 200
        body = resp.json()
        assert "cohere:command-a-plus-05-2026" in body["tool_incompatible_models"]
        # Par de erro: o campo é sempre uma lista ordenada e nunca contém
        # duplicatas (reflexo direto de um set) — nada de vazamento de tipo.
        assert body["tool_incompatible_models"] == sorted(
            TOOL_CALLING_INCOMPATIBLE_MODELS
        )


@pytest.mark.asyncio
async def test_ollama_runtime_status_reports_missing_runtime(monkeypatch):
    """O wizard precisa distinguir Ollama ausente de um servidor parado."""
    from backend.api.handlers import provider_routing

    monkeypatch.setattr(provider_routing, "_ollama_executable", lambda: None)
    monkeypatch.setattr(
        provider_routing,
        "_ollama_http_status",
        AsyncMock(return_value=(False, None)),
    )

    status = await provider_routing.ollama_runtime_status()

    assert status.installed is False
    assert status.reachable is False
    assert status.state == "missing"


@pytest.mark.asyncio
async def test_ollama_runtime_status_reports_active_install(monkeypatch):
    """Uma instalação em andamento não deve aparecer como runtime ausente."""
    from backend.api.handlers import provider_routing

    monkeypatch.setattr(provider_routing, "_ollama_executable", lambda: None)
    monkeypatch.setattr(
        provider_routing,
        "_ollama_http_status",
        AsyncMock(return_value=(False, None)),
    )
    monkeypatch.setattr(provider_routing, "_ollama_install_phase", "download")

    status = await provider_routing.ollama_runtime_status()

    assert status.installed is False
    assert status.reachable is False
    assert status.state == "installing"
    assert status.phase == "download"

    def test_registered_ollama_model_appears_in_dynamic_models(self, client):
        tag = "providers-test-tag"
        # DB real compartilhada entre execuções (ver threads.py::_get_db) —
        # limpa resíduo de uma execução anterior antes de registrar de novo.
        for existing in client.get("/provider-routing/ollama/registered").json():
            if existing["tag"] == tag:
                client.delete(f"/provider-routing/ollama/registered/{existing['id']}")

        create = client.post("/provider-routing/ollama/registered", json={"tag": tag})
        assert create.status_code == 200
        model_id = create.json()["id"]

        resp = client.get("/models/providers")
        assert resp.status_code == 200
        ids = [m["id"] for m in resp.json()["dynamic_models"]]
        assert f"ollama:{tag}" in ids

        client.delete(f"/provider-routing/ollama/registered/{model_id}")

    def test_registered_openrouter_model_appears_alongside_ollama(self, client):
        ollama_tag = "providers-test-ollama-tag"
        openrouter_tag = "providers-test-openrouter/model"
        for existing in client.get("/provider-routing/ollama/registered").json():
            if existing["tag"] == ollama_tag:
                client.delete(f"/provider-routing/ollama/registered/{existing['id']}")
        for existing in client.get("/provider-routing/openrouter/registered").json():
            if existing["tag"] == openrouter_tag:
                client.delete(
                    f"/provider-routing/openrouter/registered/{existing['id']}"
                )

        ollama_id = client.post(
            "/provider-routing/ollama/registered", json={"tag": ollama_tag}
        ).json()["id"]
        openrouter_id = client.post(
            "/provider-routing/openrouter/registered", json={"tag": openrouter_tag}
        ).json()["id"]

        resp = client.get("/models/providers")
        assert resp.status_code == 200
        ids = [m["id"] for m in resp.json()["dynamic_models"]]
        assert f"ollama:{ollama_tag}" in ids
        assert f"openrouter:{openrouter_tag}" in ids

        client.delete(f"/provider-routing/ollama/registered/{ollama_id}")
        client.delete(f"/provider-routing/openrouter/registered/{openrouter_id}")

    def test_registered_nine_router_model_appears_in_dynamic_models(self, client):
        tag = "cc/providers-test-model"
        for existing in client.get("/provider-routing/nine-router/registered").json():
            if existing["tag"] == tag:
                client.delete(
                    f"/provider-routing/nine-router/registered/{existing['id']}"
                )

        create = client.post(
            "/provider-routing/nine-router/registered", json={"tag": tag}
        )
        assert create.status_code == 200
        model_id = create.json()["id"]

        resp = client.get("/models/providers")
        assert resp.status_code == 200
        ids = [m["id"] for m in resp.json()["dynamic_models"]]
        assert f"nine_router:{tag}" in ids

        client.delete(f"/provider-routing/nine-router/registered/{model_id}")
