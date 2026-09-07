"""Testes da camada assíncrona compartilhada do Git Workbench."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import git
import pytest

from backend.services.git import GitOperationError, GitService, redact_git_output


def make_repo(path: Path) -> git.Repo:
    repo = git.Repo.init(path)
    (path / "README.md").write_text("Vectora\n", encoding="utf-8")
    repo.index.add(["README.md"])
    repo.index.commit("initial")
    return repo


@pytest.mark.asyncio
async def test_operations_same_repository_are_serialized(tmp_path: Path) -> None:
    service = GitService()
    repo = make_repo(tmp_path / "repo")
    running = 0
    maximum = 0

    async def operation() -> None:
        nonlocal running, maximum
        running += 1
        maximum = max(maximum, running)
        await asyncio.sleep(0.01)
        running -= 1

    def callback() -> None:
        asyncio.run(operation())

    await asyncio.gather(
        service.execute("one", repo, "fetch", callback),
        service.execute("two", repo, "fetch", callback),
    )

    assert maximum == 1


@pytest.mark.asyncio
async def test_lock_timeout_is_typed(tmp_path: Path) -> None:
    service = GitService()
    repo = make_repo(tmp_path / "repo")
    gate = threading.Event()

    async def hold_lock() -> None:
        await service.execute("one", repo, "fetch", gate.wait, timeout_seconds=5)

    first = asyncio.create_task(hold_lock())
    await asyncio.sleep(0.02)
    with pytest.raises(GitOperationError, match="Tempo limite") as error:
        await service.execute("two", repo, "fetch", lambda: None, timeout_seconds=0.01)
    gate.set()
    await first
    assert error.value.code == "git_lock_timeout"


def test_redact_git_output_masks_url_credentials_and_parameters() -> None:
    value = "https://user:token@example.com/repo?token=secret authorization=Bearer"
    result = redact_git_output(value)
    assert "user:***@example.com" in result
    assert "token=***" in result
    assert "authorization=***" in result
