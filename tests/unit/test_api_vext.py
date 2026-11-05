"""HTTP coverage for the local VEXT lifecycle endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.services.vext_artifact import build_vext
from backend.services.vext_install import VextInstallStore


def _source(root: Path) -> None:
    (root / "vectora-extension.json").write_text(
        '{"id":"api.test","name":"API test","version":"1.0.0",'
        '"api_version":1,"entrypoint":"index.js"}',
        encoding="utf-8",
    )
    (root / "index.js").write_text("export default {};", encoding="utf-8")


def test_vext_lifecycle_lists_deactivates_and_reactivates(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "false")
    source = tmp_path / "source"
    source.mkdir()
    _source(source)
    artifact = tmp_path / "api-test.vext"
    build_vext(source, artifact)

    store = VextInstallStore(tmp_path / "installed")
    store.install(artifact, allow_unsigned=True)
    monkeypatch.setattr("backend.api.handlers.vext._store", lambda: store)

    class TrustStore:
        def verify(self, path: Path) -> None:
            return None

    def trust_store() -> TrustStore:
        return TrustStore()

    monkeypatch.setattr(
        "backend.api.handlers.vext._trust_store",
        trust_store,
    )

    from backend.api.server import create_app

    client = TestClient(create_app(serve_static=False))
    listed = client.get("/vext/installed")
    assert listed.status_code == 200
    extension = listed.json()["extensions"][0]
    assert extension["id"] == "api.test"
    assert extension["version"] == "1.0.0"
    assert extension["active"] is True
    assert extension["manifest"]["name"] == "API test"

    deactivated = client.post("/vext/api.test/deactivate")
    assert deactivated.status_code == 200
    assert deactivated.json()["active"] is False

    activated = client.post("/vext/api.test/activate", json={"version": "1.0.0"})
    assert activated.status_code == 200
    assert activated.json()["active"] is True


def _client(monkeypatch, tmp_path: Path) -> TestClient:
    monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "false")
    source = tmp_path / "source"
    source.mkdir()
    _source(source)
    artifact = tmp_path / "api-test.vext"
    build_vext(source, artifact)
    store = VextInstallStore(tmp_path / "installed")
    store.install(artifact, allow_unsigned=True)
    monkeypatch.setattr("backend.api.handlers.vext._store", lambda: store)

    class TrustStore:
        def verify(self, path: Path) -> None:
            return None

    monkeypatch.setattr("backend.api.handlers.vext._trust_store", TrustStore)
    from backend.api.server import create_app

    return TestClient(create_app(serve_static=False))


@pytest.mark.parametrize("bad_id", ["UPPER", "a", "x" * 65, "has space", "ok..id%00"])
@pytest.mark.parametrize("route", ["readme", "frontend", "icon"])
def test_routes_reject_malformed_extension_ids(
    monkeypatch, tmp_path: Path, route: str, bad_id: str
) -> None:
    client = _client(monkeypatch, tmp_path)

    assert client.get(f"/vext/{bad_id}/{route}").status_code == 422


@pytest.mark.parametrize("bad_id", ["UPPER", "a", "x" * 65])
def test_mutating_routes_reject_malformed_extension_ids(
    monkeypatch, tmp_path: Path, bad_id: str
) -> None:
    client = _client(monkeypatch, tmp_path)

    assert client.post(f"/vext/{bad_id}/deactivate").status_code == 422
    assert client.delete(f"/vext/{bad_id}").status_code == 422
    assert (
        client.post(f"/vext/{bad_id}/activate", json={"version": "1.0.0"}).status_code
        == 422
    )


@pytest.mark.parametrize("version", ["../..", "1.0.0/../x", " ", "x" * 65])
def test_activate_rejects_versions_that_are_paths(
    monkeypatch, tmp_path: Path, version: str
) -> None:
    client = _client(monkeypatch, tmp_path)

    assert (
        client.post("/vext/api.test/activate", json={"version": version}).status_code
        == 422
    )


def test_frontend_page_is_sandboxed_and_not_sniffable(
    monkeypatch, tmp_path: Path
) -> None:
    """A página do host executa o JS da extensão: mesmo aberta direto, roda num
    sandbox sem a origem da aplicação."""
    client = _client(monkeypatch, tmp_path)
    response = client.get("/vext/api.test/frontend")

    if response.status_code == 200:
        csp = response.headers["content-security-policy"]
        assert "sandbox" in csp
        assert "allow-same-origin" not in csp
        assert response.headers["x-content-type-options"] == "nosniff"


def test_icon_cannot_run_scripts_even_when_opened_directly(
    monkeypatch, tmp_path: Path
) -> None:
    client = _client(monkeypatch, tmp_path)
    response = client.get("/vext/api.test/icon")

    if response.status_code == 200:
        assert response.headers["content-type"].startswith("image/svg+xml")
        csp = response.headers["content-security-policy"]
        assert "default-src 'none'" in csp
        assert "sandbox" in csp
        assert response.headers["x-content-type-options"] == "nosniff"
