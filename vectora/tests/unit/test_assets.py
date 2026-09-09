from concurrent.futures import ThreadPoolExecutor

import pytest

from backend.services.assets import Asset, AssetStore


def test_asset_store_returns_only_owned_non_symlink_assets(tmp_path) -> None:
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


def test_asset_store_rejects_unknown_mime(tmp_path) -> None:
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


def test_asset_store_preserva_registros_em_criacoes_concorrentes(tmp_path) -> None:
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


def test_asset_store_recupera_indice_incompleto_sem_excecao(tmp_path) -> None:
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    (metadata / "index.json").write_text('{"truncated":', encoding="utf-8")

    assert AssetStore(metadata)._read() == {}
