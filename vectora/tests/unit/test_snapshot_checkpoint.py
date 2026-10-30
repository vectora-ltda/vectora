"""Testes para a estratégia snapshot de checkpoint (A.3).

Cobre:
- create_snapshot_checkpoint: cria tarball, inclui arquivos, respeita limites
- restore_snapshot_checkpoint: extrai conteúdo sobre cwd
- gc_snapshots: limita quantidade e tamanho total de snapshots
"""

from __future__ import annotations

import io
import os
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
    # A dangling link (the target may not exist on this machine) would make
    # ``exists()`` false even if the restore had left it behind.
    assert not (restore / "redirect").is_symlink()


@pytest.mark.skipif(os.name == "nt", reason="modos POSIX não se aplicam ao Windows")
def test_restore_preserva_bit_executavel_do_snapshot(tmp_path: Path) -> None:
    """Par de erro: um script executável volta executável; um modo com setuid
    no snapshot não é restaurado com o bit."""
    archive = tmp_path / "exec.tar.gz"
    payload = b"#!/bin/sh\necho ok\n"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("scripts/build.sh")
        info.size = len(payload)
        info.mode = 0o755
        tar.addfile(info, io.BytesIO(payload))
        suid = tarfile.TarInfo("tool")
        suid.size = len(payload)
        suid.mode = 0o4755
        tar.addfile(suid, io.BytesIO(payload))

    restore = tmp_path / "restore"
    restore.mkdir()
    assert restore_snapshot_checkpoint(str(archive), str(restore))["status"] == "ok"
    assert (restore / "scripts" / "build.sh").stat().st_mode & 0o777 == 0o755
    assert (restore / "tool").stat().st_mode & 0o7000 == 0


def test_restore_snapshot_rejects_symlink_cwd_and_destination(tmp_path: Path) -> None:
    """Restore writes into a real directory but never follows a symlinked cwd
    or a destination file that is a symlink to another workspace file."""
    archive = tmp_path / "snap.tar.gz"
    payload = b"restored\n"
    with tarfile.open(archive, "w:gz") as tar:
        info = tarfile.TarInfo("a.txt")
        info.size = len(payload)
        tar.addfile(info, io.BytesIO(payload))

    real = tmp_path / "real"
    real.mkdir()
    assert restore_snapshot_checkpoint(str(archive), str(real))["status"] == "ok"
    assert (real / "a.txt").read_bytes() == payload

    workspace = tmp_path / "ws"
    workspace.mkdir()
    protected = workspace / "protected.txt"
    protected.write_bytes(b"keep\n")
    cwd_link = tmp_path / "ws-link"
    try:
        cwd_link.symlink_to(workspace, target_is_directory=True)
        (workspace / "a.txt").symlink_to(protected)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks indisponíveis neste executor")

    via_link = restore_snapshot_checkpoint(str(archive), str(cwd_link))
    assert via_link["status"] == "error"
    via_destination = restore_snapshot_checkpoint(str(archive), str(workspace))
    assert via_destination["status"] == "error"
    assert "symlink" in via_destination["message"]
    assert protected.read_bytes() == b"keep\n"
    assert (workspace / "a.txt").is_symlink()


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


def test_create_snapshot_reserva_espaco_para_o_manifesto(tmp_path: Path) -> None:
    """Par de erro: arquivos que enchem o cap total não podem deixar o manifesto
    de fora do limite; o snapshot ainda assim precisa ser criado e restaurado."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    ten_mib = 10 * 1024 * 1024
    for i in range(5):
        (workspace / f"big-{i}.bin").write_bytes(b"\x00" * ten_mib)

    result = create_snapshot_checkpoint(str(workspace), tmp_path / "snaps", "t1", "msg")
    assert result["status"] == "ok"

    restore = tmp_path / "restore"
    restore.mkdir()
    assert (
        restore_snapshot_checkpoint(result["snapshot_path"], str(restore))["status"]
        == "ok"
    )
