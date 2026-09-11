import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.services.assets import Asset, AssetStore


def test_asset_store_returns_only_owned_non_symlink_assets(tmp_path: Path) -> None:
    media = tmp_path / "image.png"
    media.write_bytes(b"png")
    store = AssetStore(tmp_path / "metadata")
    asset = store.create(
        path=media,
        owner_id="u1",
        workspace_id="w1",
        thread_id="t1",
        mime_type="image/png",
        source="upload",
    )

    assert (
        store.get(asset.id, owner_id="u1", workspace_id="w1", thread_id="t1") == asset
    )
    assert store.get(asset.id, owner_id="u2", workspace_id="w1", thread_id="t1") is None
    assert store.get(asset.id, owner_id="u1", workspace_id="w2", thread_id="t1") is None
    assert store.get(asset.id, owner_id="u1", workspace_id="w1", thread_id="t2") is None


def test_asset_store_rejects_unknown_mime(tmp_path: Path) -> None:
    path = tmp_path / "payload.bin"
    path.write_bytes(b"x")
    with pytest.raises(ValueError):
        AssetStore(tmp_path / "metadata").create(
            path=path,
            owner_id="u1",
            workspace_id="w1",
            thread_id="t1",
            mime_type="application/octet-stream",
            source="upload",
        )


def test_asset_store_preserva_registros_em_criacoes_concorrentes(
    tmp_path: Path,
) -> None:
    media = tmp_path / "image.png"
    media.write_bytes(b"png")
    store = AssetStore(tmp_path / "metadata")

    def create(index: int) -> Asset:
        return store.create(
            path=media,
            owner_id="u1",
            workspace_id="w1",
            thread_id=f"t{index}",
            mime_type="image/png",
            source="upload",
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        assets = list(executor.map(create, range(8)))

    assert all(
        store.get(asset.id, owner_id="u1", workspace_id="w1", thread_id=asset.thread_id)
        == asset
        for asset in assets
    )


def test_asset_store_recupera_indice_incompleto_sem_excecao(tmp_path: Path) -> None:
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    (metadata / "index.json").write_text('{"truncated":', encoding="utf-8")

    assert AssetStore(metadata)._read() == {}


@pytest.mark.parametrize("payload", ["[]", "null", '{"bad": []}'])
def test_asset_store_ignora_raiz_ou_registros_malformados(
    tmp_path: Path, payload: str
) -> None:
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    (metadata / "index.json").write_text(payload, encoding="utf-8")

    store = AssetStore(metadata)
    assert store._read() == {}
    assert store.get("bad", owner_id="u1", workspace_id="w1") is None


def test_asset_store_no_windows_reposiciona_descritor_antes_do_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O caminho msvcrt bloqueia sempre o byte zero, nunca o fim do arquivo."""
    import backend.services.assets as assets_module

    class FakeMsvcrt:
        LK_LOCK = 1

        def __init__(self) -> None:
            self.positions: list[int] = []

        def locking(
            self, file_descriptor: int, mode: int, number_of_bytes: int
        ) -> None:
            self.positions.append(os.lseek(file_descriptor, 0, os.SEEK_CUR))

    fake_msvcrt = FakeMsvcrt()
    monkeypatch.setattr(assets_module, "_fcntl", None)
    monkeypatch.setattr(assets_module, "_msvcrt", fake_msvcrt)
    path = tmp_path / "image.png"
    path.write_bytes(b"png")

    AssetStore(tmp_path / "metadata").create(
        path=path,
        owner_id="u1",
        workspace_id="w1",
        thread_id="t1",
        mime_type="image/png",
        source="upload",
    )

    assert fake_msvcrt.positions == [0]
