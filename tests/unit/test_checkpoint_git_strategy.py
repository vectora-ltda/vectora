"""Testes para a estratégia git de checkpoint de workspace (rewind — A.2).

Cobre ``create_git_checkpoint``/``restore_git_checkpoint``/``list_git_checkpoints``
em ``src/services/checkpoint.py``: snapshots do worktree gravados como commits
soltos em ``refs/vectora/checkpoints/<thread_id>``, sem mover HEAD/índice/branch.
"""

from __future__ import annotations

from pathlib import Path

import git
import pytest

from backend.persistence.checkpoint import (
    checkpoint_ref,
    create_git_checkpoint,
    list_git_checkpoints,
    restore_git_checkpoint,
)

# ---------------------------------------------------------------------------
# Fixtures — repos temporários
# ---------------------------------------------------------------------------


@pytest.fixture
def repo_with_commit(tmp_path: Path) -> git.Repo:
    repo = git.Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "Test User").release()
    repo.config_writer().set_value("user", "email", "test@test.com").release()
    f = tmp_path / "README.md"
    f.write_text("inicial\n")
    repo.index.add(["README.md"])
    repo.index.commit("Initial commit")
    return repo


@pytest.fixture
def empty_repo(tmp_path: Path) -> git.Repo:
    repo = git.Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "Test User").release()
    repo.config_writer().set_value("user", "email", "test@test.com").release()
    return repo


# ---------------------------------------------------------------------------
# create_git_checkpoint
# ---------------------------------------------------------------------------


class TestCreateGitCheckpoint:
    def test_creates_commit_without_moving_head(self, repo_with_commit: git.Repo):
        repo = repo_with_commit
        head_before = repo.head.commit.hexsha
        branch_before = repo.active_branch.name

        result = create_git_checkpoint(repo, "thread-1", "checkpoint 1")

        assert result["status"] == "ok"
        assert repo.head.commit.hexsha == head_before
        assert repo.active_branch.name == branch_before
        assert result["sha"] != head_before

    def test_does_not_touch_real_index_or_worktree(
        self, repo_with_commit: git.Repo, tmp_path: Path
    ):
        repo = repo_with_commit
        (tmp_path / "README.md").write_text("modificado\n")
        (tmp_path / "novo.txt").write_text("untracked\n")
        status_before = repo.git.status("--porcelain")

        create_git_checkpoint(repo, "thread-1", "checkpoint 1")

        assert repo.git.status("--porcelain") == status_before
        assert (tmp_path / "README.md").read_text() == "modificado\n"
        assert (tmp_path / "novo.txt").read_text() == "untracked\n"

    def test_captures_untracked_and_modified_files(
        self, repo_with_commit: git.Repo, tmp_path: Path
    ):
        # `write_bytes` evita a tradução de newline de `write_text` no Windows
        # (que trocaria "\n" por "\r\n"), mantendo a comparação determinística.
        repo = repo_with_commit
        (tmp_path / "README.md").write_bytes(b"modificado\n")
        (tmp_path / "novo.txt").write_bytes(b"untracked\n")

        result = create_git_checkpoint(repo, "thread-1", "checkpoint 1")
        sha = result["sha"]

        snapshot = repo.commit(sha)
        blobs = [
            item for item in snapshot.tree.traverse() if isinstance(item, git.Blob)
        ]
        blob_paths = {blob.path for blob in blobs}
        assert "README.md" in blob_paths
        assert "novo.txt" in blob_paths
        assert (snapshot.tree / "README.md").data_stream.read() == b"modificado\n"
        assert (snapshot.tree / "novo.txt").data_stream.read() == b"untracked\n"

    def test_uses_fixed_checkpoint_author(self, repo_with_commit: git.Repo):
        repo = repo_with_commit
        result = create_git_checkpoint(repo, "thread-1", "checkpoint 1")

        commit = repo.commit(result["sha"])
        assert commit.author.name == "Vectora"
        assert commit.author.email == "vectora@local"

    def test_chains_checkpoints_to_previous_one(
        self, repo_with_commit: git.Repo, tmp_path: Path
    ):
        repo = repo_with_commit

        first = create_git_checkpoint(repo, "thread-1", "checkpoint 1")
        (tmp_path / "novo.txt").write_text("v2\n")
        second = create_git_checkpoint(repo, "thread-1", "checkpoint 2")

        second_commit = repo.commit(second["sha"])
        assert [p.hexsha for p in second_commit.parents] == [first["sha"]]
        assert repo.git.rev_parse(checkpoint_ref("thread-1")).strip() == second["sha"]

    def test_works_on_repo_without_commits(self, empty_repo: git.Repo, tmp_path: Path):
        (tmp_path / "a.txt").write_text("a\n")

        result = create_git_checkpoint(empty_repo, "thread-1", "checkpoint 1")

        assert result["status"] == "ok"
        commit = empty_repo.commit(result["sha"])
        assert commit.parents == ()


# ---------------------------------------------------------------------------
# restore_git_checkpoint
# ---------------------------------------------------------------------------


class TestRestoreGitCheckpoint:
    def test_restores_modified_file_to_snapshot_content(
        self, repo_with_commit: git.Repo, tmp_path: Path
    ):
        repo = repo_with_commit
        readme = tmp_path / "README.md"
        readme.write_text("estado A\n")
        checkpoint = create_git_checkpoint(repo, "thread-1", "estado A")

        readme.write_text("estado B (descartado)\n")

        result = restore_git_checkpoint(repo, checkpoint["sha"])

        assert result["status"] == "ok"
        assert readme.read_text() == "estado A\n"

    def test_restore_does_not_move_head(
        self, repo_with_commit: git.Repo, tmp_path: Path
    ):
        repo = repo_with_commit
        head_before = repo.head.commit.hexsha
        (tmp_path / "README.md").write_text("estado A\n")
        checkpoint = create_git_checkpoint(repo, "thread-1", "estado A")

        restore_git_checkpoint(repo, checkpoint["sha"])

        assert repo.head.commit.hexsha == head_before

    def test_returns_error_for_invalid_sha(self, repo_with_commit: git.Repo):
        result = restore_git_checkpoint(repo_with_commit, "0" * 40)
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# list_git_checkpoints
# ---------------------------------------------------------------------------


class TestListGitCheckpoints:
    def test_lists_in_reverse_chronological_order(
        self, repo_with_commit: git.Repo, tmp_path: Path
    ):
        repo = repo_with_commit
        first = create_git_checkpoint(repo, "thread-1", "primeiro")
        (tmp_path / "README.md").write_text("v2\n")
        second = create_git_checkpoint(repo, "thread-1", "segundo")

        result = list_git_checkpoints(repo, "thread-1")

        assert result["status"] == "ok"
        shas = [c["sha"] for c in result["checkpoints"]]
        assert shas[0] == second["sha"]
        assert shas[1] == first["sha"]
        assert result["checkpoints"][0]["message"] == "segundo"

    def test_returns_empty_list_when_no_checkpoints_exist(
        self, repo_with_commit: git.Repo
    ) -> None:
        result = list_git_checkpoints(repo_with_commit, "thread-sem-checkpoints")
        assert result == {"status": "ok", "checkpoints": []}

    def test_does_not_mix_threads(self, repo_with_commit: git.Repo):
        repo = repo_with_commit
        create_git_checkpoint(repo, "thread-a", "a1")
        create_git_checkpoint(repo, "thread-b", "b1")

        result_a = list_git_checkpoints(repo, "thread-a")
        result_b = list_git_checkpoints(repo, "thread-b")

        messages_a = [c["message"] for c in result_a["checkpoints"]]
        messages_b = [c["message"] for c in result_b["checkpoints"]]
        # Cada ref encadeia ao HEAD original ("Initial commit"), então o
        # histórico de uma thread inclui ancestrais comuns — mas nunca os
        # checkpoints da outra thread (refs independentes não se cruzam).
        assert "a1" in messages_a
        assert "b1" not in messages_a
        assert "b1" in messages_b
        assert "a1" not in messages_b


# ---------------------------------------------------------------------------
# GitCommandNotFound — git não instalado no sistema (máquina limpa)
# ---------------------------------------------------------------------------


class TestGitCommandNotFound:
    def test_create_checkpoint_com_git_nao_instalado_nao_propaga(self) -> None:
        from unittest.mock import MagicMock

        not_found = git.GitCommandNotFound("git", FileNotFoundError("sumiu"))
        repo = MagicMock()
        repo.head.commit.hexsha = "deadbeef"
        repo.git.rev_parse.side_effect = not_found
        repo.git.read_tree.side_effect = not_found

        result = create_git_checkpoint(repo, "thread-1", "checkpoint")

        assert result["status"] == "error"

    def test_restore_checkpoint_com_git_nao_instalado_nao_propaga(self) -> None:
        from unittest.mock import MagicMock

        repo = MagicMock()
        repo.git.restore.side_effect = git.GitCommandNotFound(
            "git", FileNotFoundError("sumiu")
        )

        result = restore_git_checkpoint(repo, "0" * 40)

        assert result["status"] == "error"

    def test_list_checkpoints_com_git_nao_instalado_retorna_lista_vazia(self) -> None:
        from unittest.mock import MagicMock

        repo = MagicMock()
        repo.iter_commits.side_effect = git.GitCommandNotFound(
            "git", FileNotFoundError("sumiu")
        )

        result = list_git_checkpoints(repo, "thread-1")

        assert result == {"status": "ok", "checkpoints": []}
