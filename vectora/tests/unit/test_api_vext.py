"""HTTP coverage for the local VEXT lifecycle endpoints."""

from __future__ import annotations

from pathlib import Path

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
