"""Regression tests for the safe, idempotent Git Workbench ignore action."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from backend.api.handlers.workspaces import GitignoreAppendRequest, append_gitignore


@pytest.mark.asyncio
async def test_append_gitignore_is_idempotent_and_preserves_existing_content(
    tmp_path: Path,
) -> None:
    """Appending the same file rule twice must not duplicate or rewrite it."""
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text("# local rules\n", encoding="utf-8")
    workspace = SimpleNamespace(cwd=str(tmp_path))

    def get_workspace(_workspace_id: str) -> SimpleNamespace:
        return workspace

    registry = SimpleNamespace(get=get_workspace)

    with patch("backend.workspace.workspace.workspace_registry", registry):
        request = GitignoreAppendRequest(path="build\\cache")
        first = await append_gitignore("ws1", request)
        second = await append_gitignore("ws1", request)

    assert first.status == "ok"
    assert second.status == "ok"
    assert gitignore.read_text(encoding="utf-8") == "# local rules\n/build/cache\n"


@pytest.mark.asyncio
async def test_append_gitignore_rejects_path_traversal(tmp_path: Path) -> None:
    """A user-controlled path must never escape the workspace root."""
    workspace = SimpleNamespace(cwd=str(tmp_path))

    def get_workspace(_workspace_id: str) -> SimpleNamespace:
        return workspace

    registry = SimpleNamespace(get=get_workspace)

    with patch("backend.workspace.workspace.workspace_registry", registry):
        result = await append_gitignore(
            "ws1", GitignoreAppendRequest(path="../outside")
        )

    assert result.status == "error"
    assert not (tmp_path.parent / ".gitignore").exists()


@pytest.mark.asyncio
async def test_append_gitignore_does_not_rewrite_invalid_utf8(
    tmp_path: Path,
) -> None:
    """Invalid existing bytes must fail without replacing or rewriting them."""
    gitignore = tmp_path / ".gitignore"
    original = b"valid\xff\n"
    gitignore.write_bytes(original)
    workspace = SimpleNamespace(cwd=str(tmp_path))

    def get_workspace(_workspace_id: str) -> SimpleNamespace:
        return workspace

    registry = SimpleNamespace(get=get_workspace)

    with patch("backend.workspace.workspace.workspace_registry", registry):
        result = await append_gitignore(
            "ws1", GitignoreAppendRequest(path="build/cache")
        )

    assert result.status == "error"
    assert gitignore.read_bytes() == original


@pytest.mark.asyncio
async def test_append_gitignore_rejects_external_symlink_destination(
    tmp_path: Path,
) -> None:
    """A .gitignore symlink must not allow writes outside the workspace."""
    outside = tmp_path.parent / "external-gitignore"
    outside.write_text("# external\n", encoding="utf-8")
    gitignore = tmp_path / ".gitignore"
    try:
        gitignore.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")

    workspace = SimpleNamespace(cwd=str(tmp_path))

    def get_workspace(_workspace_id: str) -> SimpleNamespace:
        return workspace

    registry = SimpleNamespace(get=get_workspace)

    with patch("backend.workspace.workspace.workspace_registry", registry):
        result = await append_gitignore(
            "ws1", GitignoreAppendRequest(path="build/cache")
        )

    assert result.status == "error"
    assert outside.read_text(encoding="utf-8") == "# external\n"
