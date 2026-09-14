from pathlib import Path

import pytest
from _pytest.monkeypatch import MonkeyPatch

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
