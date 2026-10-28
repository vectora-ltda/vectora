"""Testes para a estratégia snapshot de checkpoint (A.3).

Cobre:
- create_snapshot_checkpoint: cria tarball, inclui arquivos, respeita limites
- restore_snapshot_checkpoint: extrai conteúdo sobre cwd
- gc_snapshots: limita quantidade e tamanho total de snapshots
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from backend.persistence.checkpoint import (
    create_snapshot_checkpoint,
    gc_snapshots,
    restore_snapshot_checkpoint,
)

# ---------------------------------------------------------------------------
# create_snapshot_checkpoint
# ---------------------------------------------------------------------------


def test_create_snapshot_creates_archive(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("world\n")

    snap_dir = tmp_path / "snaps"
    result = create_snapshot_checkpoint(str(workspace), snap_dir, "t1", "msg")

    assert result["status"] == "ok"
    assert Path(result["snapshot_path"]).is_file()
    assert result["snapshot_path"].endswith(".tar.gz")


def test_create_snapshot_contains_files(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_bytes(b"aaa")
    (workspace / "b.txt").write_bytes(b"bbb")

    snap_dir = tmp_path / "snaps"
    result = create_snapshot_checkpoint(str(workspace), snap_dir, "t1", "msg")

    with tarfile.open(result["snapshot_path"], "r:gz") as tar:
        names = tar.getnames()
    assert "a.txt" in names
    assert "b.txt" in names


def test_create_snapshot_contains_manifest(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "f.txt").write_text("x")

    snap_dir = tmp_path / "snaps"
    result = create_snapshot_checkpoint(
        str(workspace), snap_dir, "thread-42", "turn msg"
    )

    with tarfile.open(result["snapshot_path"], "r:gz") as tar:
        names = tar.getnames()
    assert ".vectora-snapshot-manifest.json" in names


def test_create_snapshot_excludes_git_dir(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    (workspace / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (workspace / "backend.py").write_text("pass\n")

    snap_dir = tmp_path / "snaps"
    result = create_snapshot_checkpoint(str(workspace), snap_dir, "t1", "msg")

    with tarfile.open(result["snapshot_path"], "r:gz") as tar:
        names = tar.getnames()
    assert not any(n.startswith(".git") for n in names)
    assert "backend.py" in names


def test_create_snapshot_excludes_node_modules(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    nm = workspace / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("module.exports = {}")
    (workspace / "app.ts").write_text("const x = 1")

    snap_dir = tmp_path / "snaps"
    result = create_snapshot_checkpoint(str(workspace), snap_dir, "t1", "msg")

    with tarfile.open(result["snapshot_path"], "r:gz") as tar:
        names = tar.getnames()
    assert not any("node_modules" in n for n in names)
    assert "app.ts" in names


def test_create_snapshot_returns_files_touched(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "x.py").write_text("pass")
    (workspace / "y.py").write_text("pass")

    snap_dir = tmp_path / "snaps"
    result = create_snapshot_checkpoint(str(workspace), snap_dir, "t1", "msg")

    assert set(result["files_touched"]) >= {"x.py", "y.py"}


def test_create_snapshot_error_on_bad_cwd(tmp_path: Path) -> None:
    snap_dir = tmp_path / "snaps"
    result = create_snapshot_checkpoint(
        str(tmp_path / "nonexistent"), snap_dir, "t1", "m"
    )
    assert result["status"] == "error"


def test_create_snapshot_rejects_symlink_destination(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    target = tmp_path / "real-snaps"
    target.mkdir()
    link = tmp_path / "snaps-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks indisponíveis neste executor")

    result = create_snapshot_checkpoint(str(workspace), link, "t1", "msg")

    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# restore_snapshot_checkpoint
# ---------------------------------------------------------------------------


def test_restore_snapshot_writes_files(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "original.txt").write_bytes(b"v1\n")

    snap_dir = tmp_path / "snaps"
    create_result = create_snapshot_checkpoint(str(workspace), snap_dir, "t1", "msg")
    assert create_result["status"] == "ok"

    # Modifica o arquivo no workspace
    (workspace / "original.txt").write_bytes(b"v2\n")

    restore = tmp_path / "restore"
    restore.mkdir()
    result = restore_snapshot_checkpoint(create_result["snapshot_path"], str(restore))

    assert result["status"] == "ok"
    assert (restore / "original.txt").read_bytes() == b"v1\n"


def test_restore_snapshot_error_on_missing_file(tmp_path: Path) -> None:
    result = restore_snapshot_checkpoint(
        str(tmp_path / "nonexistent.tar.gz"), str(tmp_path)
    )
    assert result["status"] == "error"


def test_restore_snapshot_rejects_path_traversal(tmp_path: Path) -> None:
    archive = tmp_path / "malicious.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        payload = b"owned"
        info = tarfile.TarInfo("../outside.txt")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    restore = tmp_path / "restore"
    restore.mkdir()
    result = restore_snapshot_checkpoint(str(archive), str(restore))

    assert result["status"] == "error"
    assert not (tmp_path / "outside.txt").exists()


def test_restore_snapshot_rejects_symlink_members(tmp_path: Path) -> None:
    archive = tmp_path / "symlink.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("redirect")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)

    restore = tmp_path / "restore"
    restore.mkdir()
    result = restore_snapshot_checkpoint(str(archive), str(restore))

    assert result["status"] == "error"
    assert not (restore / "redirect").exists()


# ---------------------------------------------------------------------------
# gc_snapshots
# ---------------------------------------------------------------------------


def _make_snap(snap_dir: Path, name: str, size: int) -> None:
    snap_dir.mkdir(parents=True, exist_ok=True)
    (snap_dir / name).write_bytes(b"\x00" * size)


def test_gc_removes_oldest_when_over_count(tmp_path: Path) -> None:
    snap_dir = tmp_path / "snaps"
    for i in range(5):
        _make_snap(snap_dir, f"{i:02d}.tar.gz", 100)

    removed = gc_snapshots(snap_dir, max_snapshots=3, max_bytes=10 * 1024 * 1024)
    assert removed == 2
    remaining = list(snap_dir.glob("*.tar.gz"))
    assert len(remaining) == 3


def test_gc_removes_oldest_when_over_bytes(tmp_path: Path) -> None:
    snap_dir = tmp_path / "snaps"
    # 5 arquivos de 1 KiB cada = 5 KiB total; cap 3 KiB → precisa remover 2
    for i in range(5):
        _make_snap(snap_dir, f"{i:02d}.tar.gz", 1024)

    removed = gc_snapshots(snap_dir, max_snapshots=100, max_bytes=3 * 1024)
    assert removed >= 2


def test_gc_no_op_when_under_limits(tmp_path: Path) -> None:
    snap_dir = tmp_path / "snaps"
    for i in range(3):
        _make_snap(snap_dir, f"{i:02d}.tar.gz", 100)

    removed = gc_snapshots(snap_dir, max_snapshots=10, max_bytes=10 * 1024 * 1024)
    assert removed == 0


def test_gc_returns_zero_on_missing_dir(tmp_path: Path) -> None:
    removed = gc_snapshots(tmp_path / "no_such_dir")
    assert removed == 0
