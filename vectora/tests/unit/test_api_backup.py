"""Contratos de autorização e confirmação do wizard de backup local."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_maintenance_barrier_blocks_until_release() -> None:
    import asyncio

    from backend.services.maintenance import maintenance_window, wait_until_available

    async def scenario() -> None:
        async with maintenance_window():
            waiter = asyncio.create_task(wait_until_available())
            await asyncio.sleep(0)
            assert not waiter.done()
        await waiter

    asyncio.run(scenario())


def test_backup_inspect_exige_ponte_desktop(monkeypatch) -> None:
    monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "false")
    monkeypatch.setenv("VECTORA_DESKTOP_BRIDGE_TOKEN", "bridge-token")
    from backend.api.server import create_app

    client = TestClient(create_app(serve_static=False), raise_server_exceptions=False)
    response = client.post(
        "/storage/backup/inspect", json={"archive_path": "backup.zip"}
    )
    assert response.status_code == 403


def test_backup_restore_exige_confirmacao_explicita(monkeypatch) -> None:
    monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "false")
    monkeypatch.setenv("VECTORA_DESKTOP_BRIDGE_TOKEN", "bridge-token")
    from backend.api.server import create_app

    client = TestClient(create_app(serve_static=False), raise_server_exceptions=False)
    response = client.post(
        "/storage/backup/restore",
        headers={"x-vectora-desktop-bridge": "bridge-token"},
        json={"archive_path": "backup.zip", "confirmed": False},
    )
    assert response.status_code == 400
    assert "confirmação" in response.json()["detail"]
