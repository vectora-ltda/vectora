import json
from pathlib import Path

import pytest

from backend.services.vext_artifact import build_vext
from backend.services.vext_install import VextInstallStore


def _source(root: Path, version: str) -> None:
    (root / "main.py").write_text(
        "def handle(method, params): return {'version': params.get('version')}\n",
        encoding="utf-8",
    )
    (root / "vectora-extension.json").write_text(
        json.dumps(
            {
                "id": "install.test",
                "publisher": "test",
                "name": "Install",
                "version": version,
                "api_version": 1,
                "runtime": "python",
                "entrypoint": "main.py",
            }
        ),
        encoding="utf-8",
    )


def test_install_and_rollback_are_atomic(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source, "1.0.0")
    first = tmp_path / "first.vext"
    build_vext(source, first)
    _source(source, "2.0.0")
    second = tmp_path / "second.vext"
    build_vext(source, second)

    store = VextInstallStore(tmp_path / "extensions")
    store.install(first, allow_unsigned=True)
    store.install(second, allow_unsigned=True)
    active = store.active("install.test")
    assert active is not None
    assert active.version == "2.0.0"
    store.rollback("install.test", "1.0.0", allow_unsigned=True)
    active = store.active("install.test")
    assert active is not None
    assert active.version == "1.0.0"
    assert len(store.list_installed()) == 2


def test_rollback_rejeita_unsigned_sem_opt_in(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source, "1.0.0")
    artifact = tmp_path / "first.vext"
    build_vext(source, artifact)
    store = VextInstallStore(tmp_path / "extensions")
    store.install(artifact, allow_unsigned=True)

    with pytest.raises(PermissionError, match="publisher confiável"):
        store.rollback("install.test", "1.0.0")


def test_install_rejeita_plataforma_incompativel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source, "1.0.0")
    manifest_path = source / "vectora-extension.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["platforms"] = ["windows"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    artifact = tmp_path / "first.vext"
    build_vext(source, artifact)
    store = VextInstallStore(tmp_path / "extensions")
    monkeypatch.setattr("backend.services.vext.sys.platform", "linux")

    with pytest.raises(RuntimeError, match="não suporta a plataforma linux"):
        store.install(artifact, allow_unsigned=True)


def test_deactivate_preserva_versoes_e_activate_reutiliza_artefato(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source, "1.0.0")
    artifact = tmp_path / "first.vext"
    build_vext(source, artifact)
    store = VextInstallStore(tmp_path / "extensions")
    store.install(artifact, allow_unsigned=True)

    assert store.deactivate("install.test") is True
    assert store.active("install.test") is None
    assert store.list_installed()[0].active is False
    restored = store.activate("install.test", "1.0.0", allow_unsigned=True)
    assert restored.active is True
