import pytest

from backend.services.assets import AssetStore


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
