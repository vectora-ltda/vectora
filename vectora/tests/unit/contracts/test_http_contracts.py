"""Testes para conformidade de contratos HTTP e rotas críticas."""

from fastapi.testclient import TestClient

from backend.api.server import create_app


def test_correlation_and_contract_version_headers():
    app = create_app(serve_static=False)
    client = TestClient(app)

    # Teste sem fornecer headers de correlação
    res = client.get("/health")
    assert res.status_code == 200
    assert "x-correlation-id" in res.headers
    assert "x-request-id" in res.headers
    assert res.headers["x-vectora-contract-version"] == "0.3.0"

    # Teste fornecendo correlation id customizado
    custom_cid = "test-corr-12345"
    res = client.get("/health", headers={"X-Correlation-ID": custom_cid})
    assert res.status_code == 200
    assert res.headers["x-correlation-id"] == custom_cid


def test_health_live_and_ready_endpoints():
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
    assert "storage" in data
