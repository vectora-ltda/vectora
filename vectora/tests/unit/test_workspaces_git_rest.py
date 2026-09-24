"""REST — git completo: body/amend em POST .../git/commit, e os endpoints
.../git/squash, .../git/reorder, .../git/cherry-pick.

Repo git real via tmp_path; workspace_registry.get mockado apontando pro
repo temporário.
"""

from __future__ import annotations

from pathlib import Path

import git
import pytest

from backend.api.handlers.workspaces import (
    GitCherryPickRequest,
    GitCommitRequest,
    GitReorderRequest,
    GitSquashRequest,
    git_cherry_pick_inline,
    git_commit_inline,
    git_commit_suggestion,
    git_reorder_inline,
    git_squash_inline,
)


@pytest.fixture
def ws_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[git.Repo, Path]:
    from backend.vtypes import Workspace
    from backend.workspace import workspace as ws_mod

    repo = git.Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "Test User").release()
    repo.config_writer().set_value("user", "email", "test@test.com").release()
    (tmp_path / "a.txt").write_text("a\n")
    repo.index.add(["a.txt"])
    repo.index.commit("Initial commit")

    ws = Workspace(
        id="ws-git",
        name="ws-git",
        cwd=str(tmp_path),
        created_at="2024-01-01T00:00:00+00:00",
        trusted=True,
    )
    monkeypatch.setattr(
        ws_mod.workspace_registry, "get", lambda wid: ws if wid == "ws-git" else None
    )
    return repo, tmp_path


@pytest.mark.asyncio
async def test_commit_amend_via_rest(ws_repo: tuple[git.Repo, Path]) -> None:
    repo, tmp_path = ws_repo
    original_head = repo.head.commit.hexsha
    (tmp_path / "a.txt").write_text("a\nmais\n")
    repo.index.add(["a.txt"])

    result = await git_commit_inline(
        "ws-git", GitCommitRequest(message="fix: a corrigido", amend=True)
    )

    assert result.status == "ok"
    assert repo.head.commit.hexsha != original_head
    assert len(list(repo.iter_commits())) == 1


@pytest.mark.asyncio
async def test_commit_body_via_rest(ws_repo: tuple[git.Repo, Path]) -> None:
    repo, tmp_path = ws_repo
    (tmp_path / "b.txt").write_text("b\n")
    repo.index.add(["b.txt"])

    result = await git_commit_inline(
        "ws-git", GitCommitRequest(message="feat: b", body="descricao longa")
    )

    assert result.status == "ok"
    assert repo.head.commit.message.strip() == "feat: b\n\ndescricao longa"


@pytest.mark.asyncio
async def test_commit_signoff_via_rest(ws_repo: tuple[git.Repo, Path]) -> None:
    repo, tmp_path = ws_repo
    (tmp_path / "signed.txt").write_text("signed\n")
    repo.index.add(["signed.txt"])

    result = await git_commit_inline(
        "ws-git", GitCommitRequest(message="feat: signed", signoff=True)
    )

    assert result.status == "ok"
    assert "Signed-off-by: Test User <test@test.com>" in repo.head.commit.message


@pytest.mark.asyncio
async def test_commit_suggestion_uses_staged_files(
    ws_repo: tuple[git.Repo, Path],
) -> None:
    _repo, tmp_path = ws_repo
    (tmp_path / "b.txt").write_text("b\n")
    (tmp_path / "c.txt").write_text("c\n")
    _repo.index.add(["b.txt", "c.txt"])

    result = await git_commit_suggestion("ws-git")

    assert result.title == "Atualiza 2 arquivos"
    assert result.description == "Alterações agrupadas a partir do estado staged atual."


@pytest.mark.asyncio
async def test_commit_suggestion_empty_when_nothing_staged(
    ws_repo: tuple[git.Repo, Path],
) -> None:
    result = await git_commit_suggestion("ws-git")

    assert result.title == ""
    assert result.description == ""


@pytest.mark.asyncio
async def test_commit_sem_workspace_devolve_erro(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.workspace import workspace as ws_mod

    monkeypatch.setattr(ws_mod.workspace_registry, "get", lambda wid: None)

    result = await git_commit_inline("ws-inexistente", GitCommitRequest(message="x"))

    assert result.status == "error"


@pytest.mark.asyncio
async def test_squash_via_rest(ws_repo: tuple[git.Repo, Path]) -> None:
    repo, tmp_path = ws_repo
    base = repo.head.commit.hexsha
    (tmp_path / "b.txt").write_text("b\n")
    repo.index.add(["b.txt"])
    repo.index.commit("add b")
    (tmp_path / "c.txt").write_text("c\n")
    repo.index.add(["c.txt"])
    repo.index.commit("add c")

    result = await git_squash_inline(
        "ws-git", GitSquashRequest(base_ref=base, message="feat: b e c")
    )

    assert result.status == "ok"
    assert len(list(repo.iter_commits())) == 2


@pytest.mark.asyncio
async def test_squash_base_ref_invalida_devolve_erro(
    ws_repo: tuple[git.Repo, Path],
) -> None:
    result = await git_squash_inline(
        "ws-git", GitSquashRequest(base_ref="ref-invalida", message="x")
    )

    assert result.status == "error"


@pytest.mark.asyncio
async def test_reorder_via_rest(ws_repo: tuple[git.Repo, Path]) -> None:
    repo, tmp_path = ws_repo
    (tmp_path / "b.txt").write_text("b\n")
    repo.index.add(["b.txt"])
    sha_b = repo.index.commit("add b").hexsha
    (tmp_path / "c.txt").write_text("c\n")
    repo.index.add(["c.txt"])
    sha_c = repo.index.commit("add c").hexsha

    result = await git_reorder_inline(
        "ws-git", GitReorderRequest(commits=[sha_c, sha_b])
    )

    assert result.status == "ok"
    messages = [c.message.strip() for c in repo.iter_commits()]
    assert messages[0] == "add b"
    assert messages[1] == "add c"


@pytest.mark.asyncio
async def test_reorder_lista_vazia_devolve_erro(
    ws_repo: tuple[git.Repo, Path],
) -> None:
    result = await git_reorder_inline("ws-git", GitReorderRequest(commits=[]))
    assert result.status == "error"


@pytest.mark.asyncio
async def test_cherry_pick_via_rest(ws_repo):
    repo, tmp_path = ws_repo
    repo.git.checkout("-b", "feature")
    (tmp_path / "feat.txt").write_text("feat\n")
    repo.index.add(["feat.txt"])
    sha = repo.index.commit("add feat").hexsha
    repo.git.checkout("master")

    result = await git_cherry_pick_inline("ws-git", GitCherryPickRequest(sha=sha))

    assert result.status == "ok"
    assert (tmp_path / "feat.txt").exists()


@pytest.mark.asyncio
async def test_cherry_pick_sha_invalido_devolve_erro(ws_repo):
    result = await git_cherry_pick_inline(
        "ws-git", GitCherryPickRequest(sha="sha-invalido-000")
    )
    assert result.status == "error"
