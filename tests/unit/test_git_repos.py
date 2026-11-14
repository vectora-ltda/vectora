"""Repositórios dentro de um workspace e a escolha gravada no vectora.toml."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.workspace import git_repos
from backend.workspace.git_repos import (
    discover_repos,
    normalize_repo_path,
    read_git_repo_setting,
    repo_kind,
    resolve_repo_dir,
    set_git_repo_in_toml,
    write_git_repo_setting,
)


def _init(path: Path) -> Path:
    (path / ".git").mkdir(parents=True, exist_ok=True)
    return path


def _paths(root: Path) -> list[tuple[str, str]]:
    return [(entry.path, entry.kind) for entry in discover_repos(root)]


class TestRepoKind:
    def test_pasta_com_git_e_repo(self, tmp_path: Path) -> None:
        assert repo_kind(_init(tmp_path / "a")) == "repo"

    def test_sem_git_nao_e_repo(self, tmp_path: Path) -> None:
        (tmp_path / "x").mkdir()
        assert repo_kind(tmp_path / "x") is None

    def test_git_arquivo_de_submodulo_e_de_worktree(self, tmp_path: Path) -> None:
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / ".git").write_text("gitdir: ../.git/modules/sub\n")
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: /r/.git/worktrees/wt\n")
        other = tmp_path / "o"
        other.mkdir()
        (other / ".git").write_text("gitdir: /algum/lugar\n")

        assert repo_kind(sub) == "submodule"
        assert repo_kind(wt) == "worktree"
        assert repo_kind(other) == "repo"

    def test_git_arquivo_vazio_nao_quebra(self, tmp_path: Path) -> None:
        (tmp_path / ".git").write_text("")
        assert repo_kind(tmp_path) is None


class TestDiscoverRepos:
    def test_pasta_com_varios_repositorios_e_nenhum_na_raiz(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path / "api")
        _init(tmp_path / "web")
        (tmp_path / "docs").mkdir()

        assert _paths(tmp_path) == [("api", "repo"), ("web", "repo")]

    def test_raiz_que_e_repositorio_vem_primeiro_com_path_vazio(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        _init(tmp_path / "libs" / "core")

        assert _paths(tmp_path) == [("", "root"), ("libs/core", "repo")]

    def test_submodulo_e_repositorio_aninhado_dentro_de_repositorio(
        self, tmp_path: Path
    ) -> None:
        _init(tmp_path)
        sub = tmp_path / "vendor_lib"
        sub.mkdir()
        (sub / ".git").write_text("gitdir: ../.git/modules/vendor_lib\n")
        _init(tmp_path / "tools" / "cli")

        assert _paths(tmp_path) == [
            ("", "root"),
            ("tools/cli", "repo"),
            ("vendor_lib", "submodule"),
        ]

    def test_workspace_dentro_de_um_repositorio_oferece_o_que_o_contem_como_padrao(
        self, tmp_path: Path
    ) -> None:
        outer = _init(tmp_path / "monorepo")
        workspace = outer / "packages" / "app"
        workspace.mkdir(parents=True)
        _init(workspace / "inner")

        entries = discover_repos(workspace)

        assert (entries[0].path, entries[0].name, entries[0].kind) == (
            "",
            "monorepo",
            "root",
        )
        assert [entry.path for entry in entries[1:]] == ["inner"]

    def test_ignora_pastas_pesadas_e_ocultas(self, tmp_path: Path) -> None:
        _init(tmp_path / "node_modules" / "pacote")
        _init(tmp_path / ".cache" / "repo")
        _init(tmp_path / "venv" / "lib")
        _init(tmp_path / "bom")

        assert _paths(tmp_path) == [("bom", "repo")]

    def test_respeita_a_profundidade_maxima(self, tmp_path: Path) -> None:
        _init(tmp_path / "a" / "b" / "c" / "d")  # 4 níveis: ainda entra
        _init(tmp_path / "a" / "b" / "c" / "d" / "e" / "f")  # 6: fora

        assert _paths(tmp_path) == [("a/b/c/d", "repo")]

    def test_nao_segue_link_simbolico_para_fora(self, tmp_path: Path) -> None:
        outside = _init(tmp_path / "fora")
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "atalho").symlink_to(outside)

        assert _paths(workspace) == []

    def test_limita_a_quantidade_de_repositorios(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(git_repos, "MAX_REPOS", 3)
        for index in range(8):
            _init(tmp_path / f"r{index}")

        assert len(discover_repos(tmp_path)) == 3

    def test_workspace_sem_nada_devolve_lista_vazia(self, tmp_path: Path) -> None:
        assert discover_repos(tmp_path) == []


class TestNormalizeAndResolve:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("api", "api"),
            ("./api/", "api"),
            ("a//b/./c", "a/b/c"),
            ("a\\b", "a/b"),
            ("", ""),
            ("  ", ""),
        ],
    )
    def test_normaliza(self, value: str, expected: str) -> None:
        assert normalize_repo_path(value) == expected

    @pytest.mark.parametrize(
        "value", ["/etc", "../x", "a/../../x", "C:\\dir", "c:/dir", "a\0b", None, 3]
    )
    def test_recusa_o_que_pode_escapar_do_workspace(self, value: object) -> None:
        assert normalize_repo_path(value) is None

    def test_resolve_so_repositorio_dentro_do_workspace(self, tmp_path: Path) -> None:
        _init(tmp_path / "api")
        (tmp_path / "pasta").mkdir()

        assert resolve_repo_dir(tmp_path, "api") == (tmp_path / "api").resolve()
        assert resolve_repo_dir(tmp_path, "pasta") is None  # não é repositório
        assert resolve_repo_dir(tmp_path, "nao-existe") is None
        assert resolve_repo_dir(tmp_path, "") is None
        assert resolve_repo_dir(tmp_path, "../x") is None

    def test_link_simbolico_para_repositorio_de_fora_e_recusado(
        self, tmp_path: Path
    ) -> None:
        outside = _init(tmp_path / "fora")
        workspace = tmp_path / "ws"
        workspace.mkdir()
        (workspace / "atalho").symlink_to(outside)

        assert resolve_repo_dir(workspace, "atalho") is None


TEMPLATE = """# vectora.toml — comentário do usuário
[workspace]
name = "meu"   # nome

[agent]
auto_commit = false

# [hooks]
# post_file_write = ["ruff format {file}"]
"""


class TestSetGitRepoInToml:
    def test_cria_a_secao_no_fim_sem_mexer_no_resto(self) -> None:
        result = set_git_repo_in_toml(TEMPLATE, "services/api")

        assert result.startswith(TEMPLATE)
        assert result.endswith('\n[git]\nrepo = "services/api"\n')

    def test_arquivo_vazio_ganha_so_a_secao(self) -> None:
        assert set_git_repo_in_toml("", "a") == '[git]\nrepo = "a"\n'

    def test_arquivo_sem_quebra_de_linha_final(self) -> None:
        assert set_git_repo_in_toml("[workspace]\nname = 'x'", "a") == (
            "[workspace]\nname = 'x'\n\n[git]\nrepo = \"a\"\n"
        )

    def test_troca_o_valor_existente_e_preserva_o_comentario_da_linha(self) -> None:
        text = (
            '[git]\nrepo = "velho"   # escolhido antes\n\n[agent]\nauto_commit = true\n'
        )

        result = set_git_repo_in_toml(text, "novo")

        assert (
            result
            == '[git]\nrepo = "novo"   # escolhido antes\n\n[agent]\nauto_commit = true\n'
        )

    def test_adiciona_repo_a_uma_secao_git_que_ja_existe_sem_ele(self) -> None:
        text = "[git]\n# nada ainda\n\n[agent]\nx = 1\n"

        assert set_git_repo_in_toml(text, "a") == (
            '[git]\nrepo = "a"\n# nada ainda\n\n[agent]\nx = 1\n'
        )

    def test_nao_confunde_repo_de_outra_secao(self) -> None:
        text = '[outra]\nrepo = "nao-mexer"\n\n[git]\nx = 1\n'

        result = set_git_repo_in_toml(text, "a")

        assert 'repo = "nao-mexer"' in result
        assert result.count('repo = "a"') == 1

    def test_valor_vazio_remove_a_linha_e_e_idempotente(self) -> None:
        text = '[git]\nrepo = "a"\n\n[agent]\nx = 1\n'

        removed = set_git_repo_in_toml(text, "")

        assert removed == "[git]\n\n[agent]\nx = 1\n"
        assert set_git_repo_in_toml(removed, "") == removed
        assert set_git_repo_in_toml(TEMPLATE, "") == TEMPLATE

    def test_gravar_o_mesmo_valor_nao_muda_o_texto(self) -> None:
        once = set_git_repo_in_toml(TEMPLATE, "a")
        assert set_git_repo_in_toml(once, "a") == once

    def test_escapa_aspas_e_barras(self) -> None:
        result = set_git_repo_in_toml("", 'a"b\\c')

        assert read_text_value(result) == 'a"b\\c'

    def test_preserva_fim_de_linha_crlf(self) -> None:
        result = set_git_repo_in_toml("[workspace]\r\nname = 'x'\r\n", "a")

        assert "\n" not in result.replace("\r\n", "")
        assert result.endswith('[git]\r\nrepo = "a"\r\n')

    def test_ignora_secao_git_dentro_de_array_de_tabelas_e_comentario(self) -> None:
        text = '# [git]\n# repo = "no-comentario"\n[[hooks.rules]]\nid = "x"\n'

        result = set_git_repo_in_toml(text, "a")

        assert result.endswith('\n[git]\nrepo = "a"\n')
        assert result.startswith(text)


def read_text_value(toml_text: str) -> str:
    import tomllib

    return tomllib.loads(toml_text)["git"]["repo"]


class TestReadAndWriteSetting:
    def test_sem_arquivo_devolve_vazio(self, tmp_path: Path) -> None:
        assert read_git_repo_setting(tmp_path) == ""

    def test_grava_cria_o_arquivo_e_le_de_volta(self, tmp_path: Path) -> None:
        write_git_repo_setting(tmp_path, "services/api")

        assert read_git_repo_setting(tmp_path) == "services/api"
        assert (
            tmp_path / "vectora.toml"
        ).read_text() == '[git]\nrepo = "services/api"\n'

    def test_grava_num_arquivo_existente_sem_perder_nada(self, tmp_path: Path) -> None:
        (tmp_path / "vectora.toml").write_text(TEMPLATE)

        write_git_repo_setting(tmp_path, "a")
        write_git_repo_setting(tmp_path, "b")

        text = (tmp_path / "vectora.toml").read_text()
        assert text.startswith(TEMPLATE)
        assert read_git_repo_setting(tmp_path) == "b"
        assert text.count("[git]") == 1

    def test_voltar_ao_padrao_remove_a_escolha(self, tmp_path: Path) -> None:
        write_git_repo_setting(tmp_path, "a")

        write_git_repo_setting(tmp_path, "")

        assert read_git_repo_setting(tmp_path) == ""

    def test_nao_deixa_arquivo_temporario(self, tmp_path: Path) -> None:
        write_git_repo_setting(tmp_path, "a")

        assert [entry.name for entry in tmp_path.iterdir()] == ["vectora.toml"]

    def test_valor_inseguro_no_arquivo_e_ignorado_na_leitura(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "vectora.toml").write_text('[git]\nrepo = "../fora"\n')

        assert read_git_repo_setting(tmp_path) == ""

    def test_toml_invalido_nao_derruba_a_leitura(self, tmp_path: Path) -> None:
        (tmp_path / "vectora.toml").write_text("isto não é toml [[[")

        assert read_git_repo_setting(tmp_path) == ""
