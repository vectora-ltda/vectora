"""Testes isolados do rastreador de arquivos por turno."""

from __future__ import annotations

from pathlib import Path

import git
import pytest

from backend.api import turn_files


def _repo(path: Path) -> git.Repo:
    repo = git.Repo.init(path)
    (path / "README.md").write_text("antes\n", encoding="utf-8")
    repo.index.add(["README.md"])
    repo.index.commit("initial")
    return repo


def test_compute_reports_text_changes_and_hunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setattr(
        turn_files,
        "_repo_and_scope",
        lambda _workspace_id: (repo, tmp_path, tmp_path),
    )

    snapshot = turn_files.capture("ws", "thread", "run-1")
    assert snapshot is not None
    (tmp_path / "README.md").write_text("antes\ndepois\n", encoding="utf-8")

    changes = turn_files.finalize(snapshot)

    assert [change.path for change in changes] == ["README.md"]
    assert changes[0].additions == 1
    assert changes[0].deletions == 0
    assert changes[0].hunks[0]["header"].startswith("@@")


def test_overlapping_runs_keep_independent_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setattr(
        turn_files,
        "_repo_and_scope",
        lambda _workspace_id: (repo, tmp_path, tmp_path),
    )

    first = turn_files.capture("ws", "thread", "run-a")
    second = turn_files.capture("ws", "thread", "run-b")
    assert first is not None and second is not None
    (tmp_path / "README.md").write_text("a\n", encoding="utf-8")
    turn_files.finalize(first)
    (tmp_path / "README.md").write_text("b\n", encoding="utf-8")
    turn_files.finalize(second)

    assert turn_files.latest("thread", "ws", "run-a")[0] == "run-a"
    assert turn_files.latest("thread", "ws", "run-b")[0] == "run-b"


def test_compute_failure_returns_empty_result(tmp_path: Path) -> None:
    snapshot = turn_files.TurnWorkspaceSnapshot(
        run_id="missing",
        thread_id="thread",
        workspace_id="ws",
        repo_root=tmp_path / "missing-repo",
        scope_root=tmp_path / "missing-repo",
        initial={},
    )

    assert turn_files.compute(snapshot) == []


def test_compute_includes_files_committed_during_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setattr(
        turn_files,
        "_repo_and_scope",
        lambda _workspace_id: (repo, tmp_path, tmp_path),
    )

    snapshot = turn_files.capture("ws", "thread", "run-commit")
    assert snapshot is not None
    (tmp_path / "README.md").write_text("antes\ndepois\n", encoding="utf-8")
    repo.index.add(["README.md"])
    repo.index.commit("edit during run")

    changes = turn_files.finalize(snapshot)

    assert [change.path for change in changes] == ["README.md"]
    assert changes[0].additions == 1


def test_latest_returns_active_run_without_explicit_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setattr(
        turn_files,
        "_repo_and_scope",
        lambda _workspace_id: (repo, tmp_path, tmp_path),
    )

    snapshot = turn_files.capture("ws", "thread", "run-active")
    assert snapshot is not None

    run_id, status, files = turn_files.latest("thread", "ws")

    assert run_id == "run-active"
    assert status == "active"
    assert files == []


def test_latest_ignores_empty_run_id_as_active_lookup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _repo(tmp_path)
    monkeypatch.setattr(
        turn_files,
        "_repo_and_scope",
        lambda _workspace_id: (repo, tmp_path, tmp_path),
    )

    snapshot = turn_files.capture("ws", "thread", "run-empty")
    assert snapshot is not None

    run_id, status, _files = turn_files.latest("thread", "ws", "")

    assert run_id == "run-empty"
    assert status == "active"
