"""Testes para conformidade de contratos HTTP e rotas críticas."""

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.api.server import create_app
from backend.contracts import CONTRACT_VERSION


def test_contract_version_matches_version_file() -> None:
    """Verifica o comportamento de contract version matches version file."""
    version_file = (
        Path(__file__).resolve().parents[3] / "backend" / "contracts" / "VERSION"
    )
    assert version_file.read_text(encoding="utf-8").strip() == CONTRACT_VERSION
    assert CONTRACT_VERSION == "1"


def test_correlation_and_contract_version_headers() -> None:
    """Verifica o comportamento de correlation and contract version headers."""
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


def test_correlation_sanitization_and_unhandled_exception_headers() -> None:
    """Verifica o comportamento de correlation sanitization and unhandled exception headers."""
    app = create_app(serve_static=False)

    @app.get("/test/error-route")
    async def _failing_route() -> None:
        """Executa a operação failing route."""
        raise RuntimeError("Simulated unhandled explosion")

    client = TestClient(app, raise_server_exceptions=False)

    # 1. Header inválido (caracteres proibidos ou vazio) é sanitizado gerando novo UUID
    invalid_cid = "bad/id\nwith spaces and injection"
    res = client.get("/health", headers={"X-Correlation-ID": invalid_cid})
    assert res.status_code == 200
    assert res.headers["x-correlation-id"] != invalid_cid
    assert len(res.headers["x-correlation-id"]) == 36

    # 2. Exceção não tratada retorna 500 com os 3 cabeçalhos garantidos
    custom_cid = "valid-corr-999"
    custom_rid = "valid-req-888"
    err_res = client.get(
        "/test/error-route",
        headers={"X-Correlation-ID": custom_cid, "X-Request-ID": custom_rid},
    )
    assert err_res.status_code == 500
    assert err_res.headers["x-correlation-id"] == custom_cid
    assert err_res.headers["x-request-id"] == custom_rid
    assert err_res.headers["x-vectora-contract-version"] == CONTRACT_VERSION


def test_health_live_and_ready_endpoints() -> None:
    """Verifica o comportamento de health live and ready endpoints."""
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


def test_health_ready_reports_not_ready_when_lancedb_cannot_be_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falha ao carregar o LanceDB nativo vira 503 com o diagnóstico, não 500."""
    monkeypatch.setitem(sys.modules, "backend.storage.lancedb.connection", None)
    client = TestClient(create_app(serve_static=False))

    ready_res = client.get("/health/ready")

    assert ready_res.status_code == 503
    data = ready_res.json()
    assert data["status"] == "not_ready"
    assert data["checks"]["lancedb"] is False


def test_lancedb_is_optional_only_on_windows_arm64_without_the_package() -> None:
    """O LanceDB não tem wheel para Windows ARM64; só lá a sonda não o exige."""
    from backend.api.server import lancedb_required

    assert lancedb_required("win32", "ARM64", installed=False) is False
    assert lancedb_required("win32", "ARM64", installed=True) is True
    assert lancedb_required("win32", "AMD64", installed=False) is True
    assert lancedb_required("linux", "aarch64", installed=False) is True
    assert lancedb_required("darwin", "arm64", installed=False) is True


def test_health_ready_is_ready_when_lancedb_is_not_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sem LanceDB numa plataforma que não o suporta, o app continua pronto."""
    monkeypatch.setitem(sys.modules, "backend.storage.lancedb.connection", None)
    monkeypatch.setattr("backend.api.server.lancedb_required", lambda *_a, **_k: False)
    client = TestClient(create_app(serve_static=False))

    ready_res = client.get("/health/ready")

    assert ready_res.status_code == 200
    data = ready_res.json()
    assert data["status"] == "ready"
    assert data["checks"]["lancedb"] is False


def test_health_ready_keeps_a_slow_lancedb_check_running_between_polls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A conexão fria ao LanceDB não é cancelada pelo limite da sonda.

    A primeira chamada responde `not_ready` ao estourar o limite, mas a
    verificação segue em segundo plano; a chamada seguinte já encontra o
    resultado.
    """
    import asyncio
    import types

    from fastapi import Response
    from fastapi.routing import APIRoute

    class _Tables:
        tables: tuple[str, ...] = ()

    class _Db:
        async def list_tables(self) -> _Tables:
            return _Tables()

    app = create_app(serve_static=False)
    endpoint = next(
        route.endpoint
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == "/health/ready"
    )

    async def scenario() -> tuple[list[int], bool, int]:
        release = asyncio.Event()
        connects: list[None] = []

        async def _slow_get_lancedb() -> _Db:
            connects.append(None)
            await release.wait()
            return _Db()

        fake_connection = types.SimpleNamespace(get_lancedb=_slow_get_lancedb)
        monkeypatch.setitem(
            sys.modules, "backend.storage.lancedb.connection", fake_connection
        )
        monkeypatch.setattr("backend.api.server.READINESS_TIMEOUT_SECONDS", 0.05)

        statuses: list[int] = []
        for _ in range(2):
            response = Response()
            await endpoint(response)
            statuses.append(response.status_code)
        in_flight_connects = len(connects)

        release.set()
        await asyncio.sleep(0.1)
        response = Response()
        body = await endpoint(response)
        statuses.append(response.status_code)
        return statuses, body.checks.lancedb, in_flight_connects

    statuses, lancedb_ok, in_flight_connects = asyncio.run(scenario())

    assert statuses == [503, 503, 200]
    assert lancedb_ok is True
    assert in_flight_connects == 1


def test_settings_prefs_protected_by_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifica o comportamento de settings prefs protected by auth."""
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
    """Verifica o comportamento de vectora context carries correlation."""
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
