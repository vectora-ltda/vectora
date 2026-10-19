"""Testes para conformidade de contratos HTTP e rotas críticas."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.server import create_app
from backend.contracts import CONTRACT_VERSION


def test_contract_version_matches_version_file() -> None:
    version_file = (
        Path(__file__).resolve().parents[3] / "backend" / "contracts" / "VERSION"
    )
    assert version_file.read_text(encoding="utf-8").strip() == CONTRACT_VERSION
    assert CONTRACT_VERSION == "1"


def test_correlation_and_contract_version_headers() -> None:
    app = create_app(serve_static=False)
    client = TestClient(app)

    # Teste sem fornecer headers de correlação
    res = client.get("/health")
    assert res.status_code == 200
    assert "x-correlation-id" in res.headers
    assert "x-request-id" in res.headers
    assert res.headers["x-vectora-contract-version"] == CONTRACT_VERSION

    # Teste fornecendo correlation id customizado
    custom_cid = "test-corr-12345"
    res = client.get("/health", headers={"X-Correlation-ID": custom_cid})
    assert res.status_code == 200
    assert res.headers["x-correlation-id"] == custom_cid


def test_health_live_and_ready_endpoints() -> None:
    app = create_app(serve_static=False)
    client = TestClient(app)

    live_res = client.get("/health/live")
    assert live_res.status_code == 200
    assert live_res.json()["status"] == "live"

    ready_res = client.get("/health/ready")
    assert ready_res.status_code in (200, 503)
    data = ready_res.json()
    assert data["status"] in ("ready", "not_ready")
    assert "version" in data
    assert "checks" in data
    assert "store" in data["checks"]
    assert "lancedb" in data["checks"]


def test_settings_prefs_protected_by_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "true")
    app = create_app(serve_static=False)
    client = TestClient(app)

    # /settings/flags é pública
    flags_res = client.get("/settings/flags")
    assert flags_res.status_code == 200

    # /settings/prefs exige autenticação e inclui correlation headers
    prefs_res = client.get("/settings/prefs")
    assert prefs_res.status_code == 401
    assert "x-correlation-id" in prefs_res.headers
    assert "x-request-id" in prefs_res.headers
    assert prefs_res.headers["x-vectora-contract-version"] == CONTRACT_VERSION


def test_vectora_context_carries_correlation() -> None:
    from backend.vtypes.context import VectoraContext, ctx_from_config

    ctx = ctx_from_config(
        {
            "configurable": {
                "thread_id": "t-123",
                "correlation_id": "corr-456",
                "request_id": "req-789",
            }
        }
    )
    assert ctx.thread_id == "t-123"
    assert ctx.correlation_id == "corr-456"
    assert ctx.request_id == "req-789"
