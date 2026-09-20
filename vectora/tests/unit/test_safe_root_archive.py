from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from _pytest.monkeypatch import MonkeyPatch
from fastapi import Request

from backend.rbac.safe_roots import SafeRootPersistenceError, SafeRootRegistry
from backend.vtypes import SafeRoot


def test_archived_root_is_excluded_from_authorization_and_can_be_restored(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Arquivar bloqueia acesso sem apagar o registro restaurável."""
    import backend.rbac.safe_roots as safe_roots_module

    monkeypatch.setattr(
        safe_roots_module,
        "_safe_roots_file",
        lambda: tmp_path / "safe-roots.json",
    )
    registry = SafeRootRegistry()
    root_path = tmp_path / "workspace"
    root_path.mkdir()
    root = registry.add(str(root_path), "Workspace", "admin")

    archived = registry.archive(root.id)
    assert archived is not None
    assert archived.archived_at is not None
    assert registry.is_under_safe_root(str(root_path)) is None
    assert all(item.id != root.id for item in registry.all_roots())
    assert any(item.id == root.id for item in registry.all_roots(include_archived=True))

    restored = registry.restore(root.id)
    assert restored is not None
    assert restored.archived_at is None
    authorized = registry.is_under_safe_root(str(root_path))
    assert authorized is not None
    assert authorized.id == root.id


def test_archive_failure_does_not_change_effective_state(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    """Falha de persistência não pode revogar a autorização apenas em memória."""
    import backend.rbac.safe_roots as safe_roots_module

    monkeypatch.setattr(
        safe_roots_module,
        "_safe_roots_file",
        lambda: tmp_path / "safe-roots.json",
    )
    registry = SafeRootRegistry()
    root_path = tmp_path / "workspace"
    root_path.mkdir()
    root = registry.add(str(root_path), "Workspace", "admin")

    def fail_save(_roots: dict[str, SafeRoot]) -> None:
        raise SafeRootPersistenceError("disk full")

    monkeypatch.setattr(registry, "_save_roots", fail_save)
    with pytest.raises(SafeRootPersistenceError):
        registry.archive(root.id)

    assert registry.is_under_safe_root(str(root_path)) is not None
    current = registry.get(root.id)
    assert current is not None
    assert current.archived_at is None


def test_concurrent_registry_instances_preserve_both_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Instâncias concorrentes não podem perder uma atualização da outra."""
    import backend.rbac.safe_roots as safe_roots_module

    monkeypatch.setattr(
        safe_roots_module,
        "_safe_roots_file",
        lambda: tmp_path / "safe-roots.json",
    )
    first_path = tmp_path / "first"
    second_path = tmp_path / "second"
    first_path.mkdir()
    second_path.mkdir()

    def add(path: Path) -> SafeRoot:
        return SafeRootRegistry().add(str(path), path.name, "admin")

    with ThreadPoolExecutor(max_workers=2) as pool:
        roots = list(pool.map(add, (first_path, second_path)))

    persisted = SafeRootRegistry().all_roots(include_archived=True)
    assert {root.id for root in roots}.issubset({root.id for root in persisted})


@pytest.mark.asyncio
async def test_workspace_creation_maps_safe_root_persistence_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falha ao registrar o root privilegiado vira erro HTTP 503."""
    from fastapi import HTTPException

    from backend.api.handlers import workspaces
    from backend.workspace import workspace as workspace_module

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    registry = SimpleNamespace(
        add=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SafeRootPersistenceError("disk full")
        )
    )
    monkeypatch.setattr(
        "backend.rbac.safe_roots.get_safe_root_registry", lambda: registry
    )
    monkeypatch.setattr(
        workspace_module.workspace_registry,
        "create",
        lambda *args, **kwargs: SimpleNamespace(id="ws-1", cwd=str(workspace_dir)),
    )
    request = SimpleNamespace(
        state=SimpleNamespace(user=SimpleNamespace(id="admin", role="admin"))
    )

    with pytest.raises(HTTPException) as exc_info:
        await workspaces.create_workspace(
            cast("Request", request),
            workspaces.CreateWorkspaceRequest(path=str(workspace_dir)),
        )
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_restore_endpoint_returns_archived_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restauração administrativa expõe o contrato HTTP validado."""
    from httpx import ASGITransport, AsyncClient

    from backend.api.handlers import admin
    from backend.api.server import create_app

    root = SafeRoot(
        id="root-1",
        path="/tmp/workspace",
        label="Workspace",
        created_at="2026-09-16T00:00:00+00:00",
        created_by="admin",
        archived_at="2026-09-17T00:00:00+00:00",
    )
    restored = root.model_copy(update={"archived_at": None})
    registry = SimpleNamespace(
        restore=lambda root_id: restored if root_id == root.id else None
    )
    monkeypatch.setattr(
        "backend.rbac.safe_roots.get_safe_root_registry", lambda: registry
    )
    monkeypatch.setenv("VECTORA_AUTH_REQUIRED", "false")
    app = create_app(serve_static=False)
    app.dependency_overrides[admin._get_user] = lambda: SimpleNamespace(
        id="admin", role="admin"
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(f"/admin/safe-roots/{root.id}/restore")

    assert response.status_code == 200
    assert response.json() == {
        "status": "restored",
        "root": restored.model_dump(mode="json"),
    }


@pytest.mark.asyncio
async def test_update_safe_root_maps_persistence_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Renomear uma raiz traduz falha de persistência em HTTP 503."""
    from fastapi import HTTPException

    from backend.api.handlers import admin

    registry = SimpleNamespace(
        update_label=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            SafeRootPersistenceError("disk full")
        )
    )
    monkeypatch.setattr(
        "backend.rbac.safe_roots.get_safe_root_registry", lambda: registry
    )
    request = SimpleNamespace(
        state=SimpleNamespace(user=SimpleNamespace(id="admin", role="admin"))
    )

    with pytest.raises(HTTPException) as exc_info:
        await admin.update_safe_root(
            cast("Request", request),
            "root-1",
            admin.UpdateSafeRootBody(label="Renamed"),
        )
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_create_workspace_privileged_registers_canonical_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A criação privilegiada registra a mesma pasta canônica autorizada."""
    from backend.api.handlers import workspaces
    from backend.workspace import workspace as workspace_module

    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    calls: list[tuple[str, str, str]] = []
    registry = SimpleNamespace(
        add=lambda path, label, owner: calls.append((path, label, owner))
    )
    fake_workspace = SimpleNamespace(
        id="ws-1", name="workspace", cwd=str(workspace_dir)
    )
    monkeypatch.setattr(
        "backend.rbac.safe_roots.get_safe_root_registry", lambda: registry
    )
    monkeypatch.setattr(
        workspace_module.workspace_registry,
        "create",
        lambda *args, **kwargs: fake_workspace,
    )
    monkeypatch.setattr(
        workspace_module.workspace_registry, "set_active", lambda *args, **kwargs: True
    )
    request = SimpleNamespace(
        state=SimpleNamespace(user=SimpleNamespace(id="admin", role="admin"))
    )

    response = await workspaces.create_workspace(
        cast("Request", request),
        workspaces.CreateWorkspaceRequest(path=str(workspace_dir)),
    )

    assert response.status == "ok"
    assert calls == [(str(workspace_dir.resolve()), "workspace", "admin")]
