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
    value = (
        "https://user:token@example.com/repo?token=secret "
        "authorization=Bearer bearer-secret Authorization: Bearer colon-secret "
        "Authorization: Basic basic-secret"
    )
    result = redact_git_output(value)
    assert "user:***@example.com" in result
    assert "token=***" in result
    assert "authorization=***" in result
    assert "Authorization: ***" in result
    assert "bearer-secret" not in result
    assert "colon-secret" not in result
    assert "basic-secret" not in result


@pytest.mark.asyncio
async def test_latest_retains_only_the_most_recent_operation(tmp_path: Path) -> None:
    service = GitService()
    repo = make_repo(tmp_path / "repo")

    await service.execute("workspace", repo, "fetch", lambda: "first")
    await service.execute("workspace", repo, "pull", lambda: "second")

    latest = await service.latest("workspace")

    assert latest is not None
    assert latest["operation"] == "pull"
    assert len(service._operations) == 1


@pytest.mark.asyncio
async def test_generic_callback_failure_is_terminal(tmp_path: Path) -> None:
    service = GitService()
    repo = make_repo(tmp_path / "repo")

    def fail() -> str:
        raise ValueError("token=leaked")

    with pytest.raises(ValueError):
        await service.execute("workspace", repo, "fetch", fail)

    latest = await service.latest("workspace")

    assert latest is not None
    assert latest["state"] == "failed"
    assert latest["error_code"] == "git_operation_failed"
    assert latest["error"] == "token=***"


@pytest.mark.asyncio
async def test_timeout_waits_for_callback_before_releasing_lock(tmp_path: Path) -> None:
    service = GitService()
    repo = make_repo(tmp_path / "repo")
    started = threading.Event()
    release = threading.Event()

    def blocking() -> str:
        started.set()
        release.wait()
        return "done"

    operation = asyncio.create_task(
        service.execute("workspace", repo, "fetch", blocking, timeout_seconds=0.01)
    )
    await asyncio.to_thread(started.wait, 1)
    try:
        await asyncio.sleep(0.05)
        assert not operation.done()
    finally:
        release.set()

    with pytest.raises(GitOperationError, match="Tempo limite"):
        await operation


@pytest.mark.asyncio
async def test_cancellation_waits_for_callback_before_releasing_lock(
    tmp_path: Path,
) -> None:
    service = GitService()
    repo = make_repo(tmp_path / "repo")
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def blocking() -> str:
        started.set()
        release.wait()
        finished.set()
        return "done"

    operation = asyncio.create_task(
        service.execute("workspace", repo, "fetch", blocking, timeout_seconds=5)
    )
    await asyncio.to_thread(started.wait, 1)
    operation.cancel()
    await asyncio.sleep(0)
    assert not finished.is_set()
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await operation
    assert finished.is_set()
    await service.execute("workspace", repo, "status", lambda: "ready")
