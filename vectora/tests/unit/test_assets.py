import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from backend.services.assets import Asset, AssetStore


def test_asset_store_returns_only_owned_non_symlink_assets(tmp_path: Path) -> None:
    media = tmp_path / "image.png"
    media.write_bytes(b"\x89PNG\r\n\x1a\n")
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


def test_asset_store_rejects_bytes_with_wrong_signature(tmp_path: Path) -> None:
    path = tmp_path / "payload.png"
    path.write_bytes(b"not a png")
    with pytest.raises(ValueError, match="assinatura"):
        AssetStore(tmp_path / "metadata").create(
            path=path,
            owner_id="u1",
            workspace_id="w1",
            thread_id="t1",
            mime_type="image/png",
            source="upload",
        )


def test_asset_store_remove_assets_da_thread_sem_apagar_referencias(
    tmp_path: Path,
) -> None:
    media_one = tmp_path / "one.png"
    media_two = tmp_path / "two.png"
    png = b"\x89PNG\r\n\x1a\n"
    media_one.write_bytes(png)
    media_two.write_bytes(png)
    store = AssetStore(tmp_path / "metadata")
    first = store.create(
        path=media_one,
        owner_id="u1",
        workspace_id="w1",
        thread_id="t1",
        mime_type="image/png",
        source="upload",
    )
    second = store.create(
        path=media_two,
        owner_id="u1",
        workspace_id="w1",
        thread_id="t2",
        mime_type="image/png",
        source="upload",
    )

    store.delete_thread_assets("t1")
    store.delete_thread_assets("t1")

    assert store.get(first.id, owner_id="u1", workspace_id="w1") is None
    assert store.get(second.id, owner_id="u1", workspace_id="w1") == second
    assert not media_one.exists()
    assert media_two.exists()


def test_asset_store_preserva_registros_em_criacoes_concorrentes(
    tmp_path: Path,
) -> None:
    media = tmp_path / "image.png"
    media.write_bytes(b"\x89PNG\r\n\x1a\n")
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


def test_asset_store_nao_substitui_indice_corrompido_ao_criar(
    tmp_path: Path,
) -> None:
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    index = metadata / "index.json"
    original = '{"truncated":'
    index.write_text(original, encoding="utf-8")
    media = tmp_path / "image.png"
    media.write_bytes(b"\x89PNG\r\n\x1a\n")

    with pytest.raises(RuntimeError, match="índice de assets corrompido"):
        AssetStore(metadata).create(
            path=media,
            owner_id="u1",
            workspace_id="w1",
            thread_id="t1",
            mime_type="image/png",
            source="upload",
        )

    assert index.read_text(encoding="utf-8") == original


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
    path.write_bytes(b"\x89PNG\r\n\x1a\n")

    AssetStore(tmp_path / "metadata").create(
        path=path,
        owner_id="u1",
        workspace_id="w1",
        thread_id="t1",
        mime_type="image/png",
        source="upload",
    )

    store = AssetStore(tmp_path / "metadata")
    store.delete_thread_assets("t1")

    assert fake_msvcrt.positions == [0, 0]


def test_asset_store_rejeita_caminho_indexado_fora_do_storage_root(
    tmp_path: Path,
) -> None:
    media = tmp_path / "image.png"
    media.write_bytes(b"\x89PNG\r\n\x1a\n")
    store = AssetStore(tmp_path / "metadata")
    asset = store.create(
        path=media,
        owner_id="u1",
        workspace_id="w1",
        thread_id="t1",
        mime_type="image/png",
        source="upload",
    )

    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(media.read_bytes())
    records = store._read()
    records[asset.id]["path"] = str(outside)
    store.index.write_text(json.dumps(records), encoding="utf-8")

    assert store.get(asset.id, owner_id="u1", workspace_id="w1") is None
