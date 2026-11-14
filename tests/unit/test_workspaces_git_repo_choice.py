"""Git workbench: listar os repositórios do workspace e operar no escolhido.

Repositórios git reais em tmp_path (a raiz e um aninhado); workspace_registry.get
mockado apontando para a raiz.
"""

from __future__ import annotations

from pathlib import Path

import git
import pytest
from fastapi import HTTPException, Response

from backend.api.handlers.workspaces import (
    GitignoreAppendRequest,
    SelectGitRepoRequest,
    append_gitignore,
    git_branches,
    git_repos,
    git_status,
    select_git_repo,
    workspace_git_diff,
    workspace_git_diff_file,
)
from backend.workspace.git_repos import read_git_repo_setting


def _commit(repo: git.Repo, name: str) -> None:
    repo.config_writer().set_value("user", "name", "Test").release()
    repo.config_writer().set_value("user", "email", "t@t.com").release()
    root = Path(repo.working_tree_dir or "")
    (root / name).write_text("x\n")
    repo.index.add([name])
    repo.index.commit(f"add {name}")


def _register(
    monkeypatch: pytest.MonkeyPatch, cwd: Path, *, trusted: bool = True
) -> None:
    from backend.vtypes import Workspace
    from backend.workspace import workspace as ws_mod

    ws = Workspace(
        id="ws-1",
        name="ws-1",
        cwd=str(cwd),
        created_at="2024-01-01T00:00:00+00:00",
        trusted=trusted,
    )
    monkeypatch.setattr(
        ws_mod.workspace_registry, "get", lambda wid: ws if wid == "ws-1" else None
    )


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Raiz com repositório e um repositório aninhado com uma branch própria."""
    root_repo = git.Repo.init(tmp_path)
    _commit(root_repo, "raiz.txt")
    inner = tmp_path / "services" / "api"
    inner.mkdir(parents=True)
    inner_repo = git.Repo.init(inner)
    _commit(inner_repo, "api.txt")
    inner_repo.create_head("feature-da-api")
    _register(monkeypatch, tmp_path)
    return tmp_path


@pytest.mark.asyncio
async def test_lista_a_raiz_e_o_repositorio_aninhado(workspace: Path) -> None:
    result = await git_repos("ws-1")

    assert [(r.path, r.kind) for r in result.repos] == [
        ("", "root"),
        ("services/api", "repo"),
    ]
    assert result.selected == ""


@pytest.mark.asyncio
async def test_workspace_desconhecido_responde_404() -> None:
    with pytest.raises(HTTPException) as error:
        await git_repos("nao-existe")

    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_sem_escolha_as_rotas_operam_na_raiz(workspace: Path) -> None:
    branches = await git_branches("ws-1")

    assert "feature-da-api" not in branches.branches


@pytest.mark.asyncio
async def test_escolher_grava_no_toml_e_as_rotas_passam_a_operar_no_repositorio(
    workspace: Path,
) -> None:
    result = await select_git_repo("ws-1", SelectGitRepoRequest(repo="services/api"))

    assert result.selected == "services/api"
    assert read_git_repo_setting(workspace) == "services/api"
    assert (workspace / "vectora.toml").read_text() == '[git]\nrepo = "services/api"\n'
    branches = await git_branches("ws-1")
    assert "feature-da-api" in branches.branches
    status = await git_status("ws-1")
    assert status.is_git_repo is True


@pytest.mark.asyncio
async def test_voltar_ao_padrao_volta_a_raiz(workspace: Path) -> None:
    await select_git_repo("ws-1", SelectGitRepoRequest(repo="services/api"))

    result = await select_git_repo("ws-1", SelectGitRepoRequest(repo=""))

    assert result.selected == ""
    assert "feature-da-api" not in (await git_branches("ws-1")).branches


@pytest.mark.asyncio
async def test_escolha_que_deixou_de_ser_repositorio_volta_ao_padrao_sem_erro(
    workspace: Path,
) -> None:
    await select_git_repo("ws-1", SelectGitRepoRequest(repo="services/api"))
    import shutil

    shutil.rmtree(workspace / "services" / "api" / ".git")

    result = await git_repos("ws-1")
    branches = await git_branches("ws-1")

    assert result.selected == ""
    assert "feature-da-api" not in branches.branches


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value", ["../fora", "/etc", "services", "nao-existe", "C:\\x", "services/../.."]
)
async def test_caminho_invalido_ou_que_nao_e_repositorio_responde_400_sem_gravar(
    workspace: Path, value: str
) -> None:
    with pytest.raises(HTTPException) as error:
        await select_git_repo("ws-1", SelectGitRepoRequest(repo=value))

    assert error.value.status_code == 400
    assert not (workspace / "vectora.toml").exists()


@pytest.mark.asyncio
async def test_workspace_nao_confiado_responde_403_sem_gravar(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _register(monkeypatch, workspace, trusted=False)

    with pytest.raises(HTTPException) as error:
        await select_git_repo("ws-1", SelectGitRepoRequest(repo="services/api"))

    assert error.value.status_code == 403
    assert not (workspace / "vectora.toml").exists()


@pytest.mark.asyncio
async def test_escolher_preserva_o_resto_do_vectora_toml(workspace: Path) -> None:
    original = '# meu projeto\n[workspace]\nname = "x"\n\n[agent]\nauto_commit = true\n'
    (workspace / "vectora.toml").write_text(original)

    await select_git_repo("ws-1", SelectGitRepoRequest(repo="services/api"))

    text = (workspace / "vectora.toml").read_text()
    assert text.startswith(original)
    assert text.endswith('[git]\nrepo = "services/api"\n')


@pytest.mark.asyncio
async def test_pasta_com_varios_repositorios_e_nenhum_na_raiz(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name, branch in (("api", "so-da-api"), ("web", "so-da-web")):
        repo_dir = tmp_path / name
        repo_dir.mkdir()
        repo = git.Repo.init(repo_dir)
        _commit(repo, f"{name}.txt")
        repo.create_head(branch)
    _register(monkeypatch, tmp_path)

    sem_escolha = await git_status("ws-1")
    listed = await git_repos("ws-1")
    await select_git_repo("ws-1", SelectGitRepoRequest(repo="web"))
    branches = await git_branches("ws-1")

    assert sem_escolha.is_git_repo is False
    assert [r.path for r in listed.repos] == ["api", "web"]
    assert "so-da-web" in branches.branches
    assert "so-da-api" not in branches.branches


@pytest.mark.asyncio
async def test_o_diff_e_o_diff_de_arquivo_seguem_o_repositorio_escolhido(
    workspace: Path,
) -> None:
    (workspace / "raiz.txt").write_text("mudou na raiz\n")
    (workspace / "services" / "api" / "api.txt").write_text("mudou na api\n")

    na_raiz = await workspace_git_diff("ws-1", Response())
    await select_git_repo("ws-1", SelectGitRepoRequest(repo="services/api"))
    na_api = await workspace_git_diff("ws-1", Response())
    arquivo = await workspace_git_diff_file("ws-1", "api.txt")

    # Na raiz, o repositório aninhado aparece como pasta não rastreada, não como arquivos.
    assert "raiz.txt" in [file.path for file in na_raiz.files]
    assert "api.txt" not in [file.path for file in na_raiz.files]
    assert [file.path for file in na_api.files] == ["api.txt"]
    assert arquivo.hunks


@pytest.mark.asyncio
async def test_o_gitignore_do_painel_e_o_do_repositorio_escolhido(
    workspace: Path,
) -> None:
    sem_escolha = await append_gitignore(
        "ws-1", GitignoreAppendRequest(path="build.log", is_folder=False)
    )
    await select_git_repo("ws-1", SelectGitRepoRequest(repo="services/api"))
    escolhido = await append_gitignore(
        "ws-1", GitignoreAppendRequest(path="cache.tmp", is_folder=False)
    )

    assert (sem_escolha.status, escolhido.status) == ("ok", "ok")
    assert (workspace / ".gitignore").read_text() == "/build.log\n"
    assert (workspace / "services" / "api" / ".gitignore").read_text() == "/cache.tmp\n"
