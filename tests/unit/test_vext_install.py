import json
from pathlib import Path

import pytest
from nacl.signing import SigningKey

from backend.services.vext_artifact import build_vext
from backend.services.vext_install import VextInstallStore
from backend.services.vext_registry import VextTrustStore


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


def test_active_revalida_revogacao_antes_de_servir_artefato(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source, "1.0.0")
    key = SigningKey.generate()
    artifact = tmp_path / "signed.vext"
    build_vext(source, artifact, signing_key=key)
    trust = VextTrustStore()
    record = trust.add("test", key.verify_key)
    store = VextInstallStore(tmp_path / "extensions")
    store.install(artifact, trust_store=trust)
    assert store.active("install.test", trust_store=trust) is not None
    trust.revoke("test", record.fingerprint)
    with pytest.raises(PermissionError, match="revogado"):
        store.active("install.test", trust_store=trust)


_BAD_IDS = ["..", ".", "../x", "a/b", r"a\b", "", "A.b", "x" * 65, "a", "a/..", "/etc"]
_BAD_VERSIONS = ["..", "../..", "1.0.0/../../x", "", "a b", "x" * 65, "/etc", r"1\..\2"]


@pytest.mark.parametrize("extension_id", _BAD_IDS)
def test_store_rejects_extension_ids_that_could_leave_the_store(
    tmp_path: Path, extension_id: str
) -> None:
    store = VextInstallStore(tmp_path / "installed")

    with pytest.raises(ValueError, match="id"):
        store.active(extension_id)
    with pytest.raises(ValueError, match="id"):
        store.deactivate(extension_id)
    with pytest.raises(ValueError, match="id"):
        store.uninstall(extension_id)
    with pytest.raises(ValueError, match="id"):
        store.activate(extension_id, "1.0.0", allow_unsigned=True)


@pytest.mark.parametrize("version", _BAD_VERSIONS)
def test_store_rejects_versions_that_could_leave_the_store(
    tmp_path: Path, version: str
) -> None:
    store = VextInstallStore(tmp_path / "installed")

    with pytest.raises(ValueError, match="versão"):
        store.activate("install.test", version, allow_unsigned=True)


def test_uninstall_with_a_traversal_id_keeps_the_store_intact(tmp_path: Path) -> None:
    store = VextInstallStore(tmp_path / "installed")
    store.versions.mkdir(parents=True)
    keep = store.versions / "keep.txt"
    keep.write_text("x", encoding="utf-8")

    with pytest.raises(ValueError):
        store.uninstall("..")

    assert keep.exists()


def test_an_artifact_whose_version_is_a_path_is_refused_by_the_manifest(
    tmp_path: Path,
) -> None:
    """O manifesto recusa a versão antes de qualquer arquivo ir para o disco."""
    source = tmp_path / "source"
    source.mkdir()
    _source(source, "../../escape")

    with pytest.raises(ValueError, match="manifesto"):
        build_vext(source, tmp_path / "evil.vext")

    assert not (tmp_path / "escape").exists()


def test_valid_ids_and_versions_are_still_accepted(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _source(source, "1.2.3-rc.1+build.5")
    artifact = tmp_path / "ok.vext"
    build_vext(source, artifact)
    store = VextInstallStore(tmp_path / "installed")

    installed = store.install(artifact, allow_unsigned=True)

    assert installed.version == "1.2.3-rc.1+build.5"
