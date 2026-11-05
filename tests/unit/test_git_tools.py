"""Git completo — amend/squash/reorder/cherry-pick, fetch/merge/revert/
compare/resolve_conflict/check_hooks, e checkout com create=True.

Repos temporários reais via gitpython; funções ``_git_*_impl`` testadas
diretamente.
"""

from __future__ import annotations

from pathlib import Path

import git
import pytest

from backend.tools.git import (
    _git_checkout_impl,
    _git_cherry_pick_impl,
    _git_commit_impl,
    _git_compare_impl,
    _git_fetch_impl,
    _git_merge_impl,
    _git_reorder_impl,
    _git_resolve_conflict_impl,
    _git_revert_impl,
    _git_squash_impl,
    _run_pre_commit_hooks,
)


def _commit_file(repo: git.Repo, path: Path, name: str, content: str) -> str:
    f = path / name
    f.write_text(content)
    repo.index.add([name])
    return repo.index.commit(f"add {name}").hexsha


@pytest.fixture
def repo_with_history(tmp_path: Path) -> git.Repo:
    repo = git.Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "Test User").release()
    repo.config_writer().set_value("user", "email", "test@test.com").release()
    _commit_file(repo, tmp_path, "a.txt", "a\n")
    return repo


# ---------------------------------------------------------------------------
# git_commit: amend + body
# ---------------------------------------------------------------------------


class TestCommitAmendBody:
    def test_amend_substitui_o_ultimo_commit(self, repo_with_history, tmp_path):
        repo = repo_with_history
        original_hash = repo.head.commit.hexsha
        (tmp_path / "a.txt").write_text("a\nmais\n")
        repo.index.add(["a.txt"])

        result = _git_commit_impl(repo, message="add a (corrigido)", amend=True)

        assert result["status"] == "ok"
        assert result["amended"] is True
        assert len(repo.heads.master.commit.parents) == 0  # ainda 1 commit só
        assert repo.head.commit.hexsha != original_hash

    def test_amend_sem_commit_anterior_falha_com_mensagem_clara(self, tmp_path):
        repo = git.Repo.init(tmp_path)
        repo.config_writer().set_value("user", "name", "T").release()
        repo.config_writer().set_value("user", "email", "t@t.com").release()

        result = _git_commit_impl(repo, message="x", amend=True)

        assert result["status"] == "error"
        assert "anterior" in result["message"].lower()

    def test_body_concatena_title_e_body(self, tmp_path):
        repo = git.Repo.init(tmp_path)
        repo.config_writer().set_value("user", "name", "T").release()
        repo.config_writer().set_value("user", "email", "t@t.com").release()
        (tmp_path / "a.txt").write_text("a\n")
        repo.index.add(["a.txt"])

        result = _git_commit_impl(repo, message="feat: a", body="detalhes aqui")

        assert result["status"] == "ok"
        assert result["message"] == "feat: a\n\ndetalhes aqui"

    def test_sem_body_continua_funcionando(self, tmp_path):
        repo = git.Repo.init(tmp_path)
        repo.config_writer().set_value("user", "name", "T").release()
        repo.config_writer().set_value("user", "email", "t@t.com").release()
        (tmp_path / "a.txt").write_text("a\n")
        repo.index.add(["a.txt"])

        result = _git_commit_impl(repo, message="feat: a")

        assert result["status"] == "ok"
        assert result["message"] == "feat: a"


# ---------------------------------------------------------------------------
# git_squash
# ---------------------------------------------------------------------------


class TestSquash:
    def test_squasha_multiplos_commits_numa_mensagem_so(
        self, repo_with_history, tmp_path
    ):
        repo = repo_with_history
        base = repo.head.commit.hexsha
        _commit_file(repo, tmp_path, "b.txt", "b\n")
        _commit_file(repo, tmp_path, "c.txt", "c\n")

        result = _git_squash_impl(repo, base_ref=base, message="feat: b e c")

        assert result["status"] == "ok"
        assert len(list(repo.iter_commits())) == 2  # base + squash
        assert (tmp_path / "b.txt").exists()
        assert (tmp_path / "c.txt").exists()

    def test_base_ref_invalida_falha_sem_alterar_o_repo(
        self, repo_with_history, tmp_path
    ):
        repo = repo_with_history
        head_before = repo.head.commit.hexsha

        result = _git_squash_impl(repo, base_ref="ref-inexistente", message="x")

        assert result["status"] == "error"
        assert repo.head.commit.hexsha == head_before

    def test_base_ref_igual_ao_head_nao_squasha_nada(self, repo_with_history):
        repo = repo_with_history
        head = repo.head.commit.hexsha

        result = _git_squash_impl(repo, base_ref=head, message="x")

        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# git_reorder
# ---------------------------------------------------------------------------


class TestReorder:
    def test_reordena_dois_commits_locais(self, repo_with_history, tmp_path):
        repo = repo_with_history
        sha_b = _commit_file(repo, tmp_path, "b.txt", "b\n")
        sha_c = _commit_file(repo, tmp_path, "c.txt", "c\n")

        result = _git_reorder_impl(repo, commits=[sha_c, sha_b])

        assert result["status"] == "ok"
        messages = [c.message.strip() for c in repo.iter_commits()]
        # HEAD (mais recente) é o último aplicado — b.txt, conforme pedido.
        assert messages[0] == "add b.txt"
        assert messages[1] == "add c.txt"

    def test_lista_vazia_falha(self, repo_with_history):
        result = _git_reorder_impl(repo_with_history, commits=[])
        assert result["status"] == "error"

    def test_commit_raiz_sem_parent_falha_com_mensagem_clara(self, repo_with_history):
        repo = repo_with_history
        root_sha = repo.head.commit.hexsha

        result = _git_reorder_impl(repo, commits=[root_sha])

        assert result["status"] == "error"
        assert "raiz" in result["message"].lower()

    def test_conflito_aborta_e_restaura_head_original(
        self, repo_with_history, tmp_path
    ):
        repo = repo_with_history
        # dois commits que tocam a MESMA linha do mesmo arquivo → conflito
        # garantido ao reordenar (cherry-pick de b sobre c, fora de ordem).
        (tmp_path / "a.txt").write_text("versao-b\n")
        repo.index.add(["a.txt"])
        sha_b = repo.index.commit("versao b").hexsha
        (tmp_path / "a.txt").write_text("versao-c\n")
        repo.index.add(["a.txt"])
        sha_c = repo.index.commit("versao c").hexsha
        original_head = repo.head.commit.hexsha

        result = _git_reorder_impl(repo, commits=[sha_c, sha_b])

        assert result["status"] == "error"
        assert repo.head.commit.hexsha == original_head


# ---------------------------------------------------------------------------
# git_cherry_pick
# ---------------------------------------------------------------------------


class TestCherryPick:
    def test_cherry_pick_aplica_commit_de_outra_branch(
        self, repo_with_history, tmp_path
    ):
        repo = repo_with_history
        repo.git.checkout("-b", "feature")
        sha = _commit_file(repo, tmp_path, "feat.txt", "feat\n")
        repo.git.checkout("master")

        result = _git_cherry_pick_impl(repo, sha=sha)

        assert result["status"] == "ok"
        assert (tmp_path / "feat.txt").exists()

    def test_cherry_pick_ja_aplicado_e_idempotente_nao_duplica(
        self, repo_with_history, tmp_path
    ):
        repo = repo_with_history
        repo.git.checkout("-b", "feature")
        sha = _commit_file(repo, tmp_path, "feat.txt", "feat\n")
        repo.git.checkout("master")
        _git_cherry_pick_impl(repo, sha=sha)
        count_before = len(list(repo.iter_commits()))

        result = _git_cherry_pick_impl(repo, sha=sha)

        assert result["status"] == "error"
        assert len(list(repo.iter_commits())) == count_before

    def test_no_commit_so_stageia_sem_criar_commit(self, repo_with_history, tmp_path):
        repo = repo_with_history
        repo.git.checkout("-b", "feature")
        sha = _commit_file(repo, tmp_path, "feat.txt", "feat\n")
        repo.git.checkout("master")
        count_before = len(list(repo.iter_commits()))

        result = _git_cherry_pick_impl(repo, sha=sha, no_commit=True)

        assert result["status"] == "ok"
        assert len(list(repo.iter_commits())) == count_before
        assert repo.is_dirty(index=True)


# ---------------------------------------------------------------------------
# git_fetch / git_merge / git_revert / git_compare / git_resolve_conflict
# ---------------------------------------------------------------------------


class TestFetchMergeRevertCompare:
    def test_fetch_remote_inexistente_falha_com_mensagem_clara(self, repo_with_history):
        result = _git_fetch_impl(repo_with_history, remote="origin")
        assert result["status"] == "error"

    def test_merge_fast_forward(self, repo_with_history, tmp_path):
        repo = repo_with_history
        repo.git.checkout("-b", "feature")
        _commit_file(repo, tmp_path, "feat.txt", "feat\n")
        repo.git.checkout("master")

        result = _git_merge_impl(repo, branch="feature")

        assert result["status"] == "ok"
        assert (tmp_path / "feat.txt").exists()

    def test_merge_com_conflito_devolve_arquivos_conflitantes(
        self, repo_with_history, tmp_path
    ):
        repo = repo_with_history
        repo.git.checkout("-b", "feature")
        (tmp_path / "a.txt").write_text("versao-feature\n")
        repo.index.add(["a.txt"])
        repo.index.commit("feature muda a.txt")
        repo.git.checkout("master")
        (tmp_path / "a.txt").write_text("versao-master\n")
        repo.index.add(["a.txt"])
        repo.index.commit("master muda a.txt")

        result = _git_merge_impl(repo, branch="feature")

        assert result["status"] == "conflict"
        assert "a.txt" in result["conflicted_files"]

    def test_revert_cria_commit_inverso(self, repo_with_history, tmp_path):
        repo = repo_with_history
        sha = _commit_file(repo, tmp_path, "b.txt", "b\n")

        result = _git_revert_impl(repo, sha=sha)

        assert result["status"] == "ok"
        assert not (tmp_path / "b.txt").exists()

    def test_revert_no_commit_so_stageia(self, repo_with_history, tmp_path):
        repo = repo_with_history
        sha = _commit_file(repo, tmp_path, "b.txt", "b\n")
        count_before = len(list(repo.iter_commits()))

        result = _git_revert_impl(repo, sha=sha, no_commit=True)

        assert result["status"] == "ok"
        assert len(list(repo.iter_commits())) == count_before

    def test_compare_lista_arquivos_alterados_entre_dois_refs(
        self, repo_with_history, tmp_path
    ):
        repo = repo_with_history
        base = repo.head.commit.hexsha
        _commit_file(repo, tmp_path, "b.txt", "b\n")
        head = repo.head.commit.hexsha

        result = _git_compare_impl(repo, base=base, head=head)

        assert result["status"] == "ok"
        paths = {f["path"] for f in result["files"]}
        assert "b.txt" in paths

    def test_compare_sem_diferenca_devolve_lista_vazia(self, repo_with_history):
        head = repo_with_history.head.commit.hexsha
        result = _git_compare_impl(repo_with_history, base=head, head=head)
        assert result["status"] == "ok"
        assert result["files"] == []

    def test_resolve_conflict_ours(self, repo_with_history, tmp_path):
        repo = repo_with_history
        repo.git.checkout("-b", "feature")
        (tmp_path / "a.txt").write_text("versao-feature\n")
        repo.index.add(["a.txt"])
        repo.index.commit("feature muda a.txt")
        repo.git.checkout("master")
        (tmp_path / "a.txt").write_text("versao-master\n")
        repo.index.add(["a.txt"])
        repo.index.commit("master muda a.txt")
        merge_result = _git_merge_impl(repo, branch="feature")
        assert merge_result["status"] == "conflict"

        result = _git_resolve_conflict_impl(repo, path="a.txt", strategy="ours")

        assert result["status"] == "ok"
        assert (tmp_path / "a.txt").read_text() == "versao-master\n"

    def test_resolve_conflict_strategy_invalida_falha(self, repo_with_history):
        result = _git_resolve_conflict_impl(
            repo_with_history, path="a.txt", strategy="invalida"
        )
        assert result["status"] == "error"


# ---------------------------------------------------------------------------
# git_checkout create=True
# ---------------------------------------------------------------------------


class TestCheckoutCreate:
    def test_create_true_cria_e_troca_para_branch_nova(self, repo_with_history):
        result = _git_checkout_impl(repo_with_history, ref="nova-branch", create=True)

        assert result["status"] == "ok"
        assert result["branch"] == "nova-branch"
        assert repo_with_history.active_branch.name == "nova-branch"

    def test_create_false_mantem_comportamento_atual(self, repo_with_history):
        repo_with_history.create_head("existente")
        result = _git_checkout_impl(repo_with_history, ref="existente")
        assert result["status"] == "ok"
        assert result["branch"] == "existente"


# ---------------------------------------------------------------------------
# git_check_hooks (dry-run de pre-commit)
# ---------------------------------------------------------------------------


class TestCheckHooks:
    def test_sem_hook_configurado_passa(self, repo_with_history):
        result = _run_pre_commit_hooks(repo_with_history)
        assert result["passed"] is True
