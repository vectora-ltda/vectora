"""Testes de `check_no_plan_comments.py` â€” hook de pre-commit que barra
proveniÃªncia de processo (plano ou ferramenta de IA) em cÃ³digo-fonte.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from check_no_plan_comments import _find_violations, main

pytestmark = pytest.mark.usefixtures("tmp_path")


def _write(tmp_path: Path, name: str, content: str) -> str:
    """Executa a operação write."""
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return str(path)


class TestPlanoDeteccao:
    def test_bloqueia_sprint_numerado(self, tmp_path: Path) -> None:
        """Verifica o comportamento de bloqueia sprint numerado."""
        path = _write(tmp_path, "a.py", "# Sprint 3: ajusta cache\nx = 1\n")
        assert _find_violations(path) == [(1, "# Sprint 3: ajusta cache")]

    def test_bloqueia_sprint_com_letra(self, tmp_path: Path) -> None:
        """Verifica o comportamento de bloqueia sprint com letra."""
        path = _write(tmp_path, "a.py", "# Sprint E: enxuga o build\nx = 1\n")
        assert len(_find_violations(path)) == 1

    def test_erro_borda_sprint_com_letra_minuscula_nao_bloqueia(
        self, tmp_path: Path
    ) -> None:
        """Verifica o comportamento de erro borda sprint com letra minuscula nao bloqueia."""
        path = _write(tmp_path, "a.py", "# sprint e atualiza o cliente\nx = 1\n")
        assert _find_violations(path) == []

    def test_bloqueia_bloco_com_letra_maiuscula(self, tmp_path: Path) -> None:
        """Verifica o comportamento de bloqueia bloco com letra maiuscula."""
        path = _write(tmp_path, "a.ts", "// Bloco T10.4 concluÃ­do\nconst x = 1;\n")
        assert len(_find_violations(path)) == 1

    def test_bloqueia_fase_com_sufixo(self, tmp_path: Path) -> None:
        """Verifica o comportamento de bloqueia fase com sufixo."""
        path = _write(tmp_path, "a.py", "# fase-2a do rollout\nx = 1\n")
        assert len(_find_violations(path)) == 1

    def test_erro_borda_bloco_minusculo_nao_bloqueia(self, tmp_path: Path) -> None:
        """Verifica o comportamento de erro borda bloco minusculo nao bloqueia."""
        path = _write(tmp_path, "a.py", "# separa em um bloco de cÃ³digo sÃ³\nx = 1\n")
        assert _find_violations(path) == []


class TestFerramentaIaDeteccao:
    def test_bloqueia_coderabbit_case_insensitive(self, tmp_path: Path) -> None:
        """Verifica o comportamento de bloqueia coderabbit case insensitive."""
        path = _write(tmp_path, "a.tsx", "// Addressed by CodeRabbit\nconst x = 1;\n")
        assert len(_find_violations(path)) == 1

    def test_bloqueia_claude_code(self, tmp_path: Path) -> None:
        """Verifica o comportamento de bloqueia claude code."""
        path = _write(tmp_path, "a.py", "# corrigido via Claude Code\nx = 1\n")
        assert len(_find_violations(path)) == 1

    def test_bloqueia_copilot(self, tmp_path: Path) -> None:
        """Verifica o comportamento de bloqueia copilot."""
        path = _write(tmp_path, "a.js", "// sugestÃ£o do copilot\nconst x = 1;\n")
        assert len(_find_violations(path)) == 1

    def test_erro_borda_comentario_dentro_de_interpolacao_de_template_bloqueia(
        self, tmp_path: Path
    ) -> None:
        """Verifica o comportamento de erro borda comentario dentro de interpolacao de template bloqueia."""
        path = _write(tmp_path, "a.ts", "const value = `${/* Claude */ 1}`;\n")
        assert len(_find_violations(path)) == 1

    def test_erro_borda_nome_de_variavel_claude_nao_bloqueia_sozinho(
        self, tmp_path: Path
    ) -> None:
        """Verifica o comportamento de erro borda nome de variavel claude nao bloqueia sozinho."""
        path = _write(
            tmp_path, "a.py", "# nenhuma referÃªncia de ferramenta aqui\nx = 1\n"
        )
        assert _find_violations(path) == []

    def test_erro_borda_identificador_claude_em_codigo_real_nao_bloqueia(
        self, tmp_path: Path
    ) -> None:
        """Verifica o comportamento de erro borda identificador claude em codigo real nao bloqueia."""
        path = _write(tmp_path, "a.py", "claude = object()\n")
        assert _find_violations(path) == []

    def test_erro_borda_string_com_id_de_modelo_claude_nao_bloqueia(
        self, tmp_path: Path
    ) -> None:
        """Verifica o comportamento de erro borda string com id de modelo claude nao bloqueia."""
        path = _write(tmp_path, "a.py", 'model = "claude-3-5-sonnet-latest"\n')
        assert _find_violations(path) == []

    def test_erro_borda_referencia_a_claude_md_nao_bloqueia(
        self, tmp_path: Path
    ) -> None:
        """Verifica o comportamento de erro borda referencia a claude md nao bloqueia."""
        path = _write(tmp_path, "a.py", "# regra vem de CLAUDE.md Â§1\nx = 1\n")
        assert _find_violations(path) == []


class TestTrackerDeteccao:
    def test_nao_bloqueia_cor_hexadecimal_css(self, tmp_path: Path) -> None:
        """Verifica o comportamento de nao bloqueia cor hexadecimal css."""
        path = _write(tmp_path, "a.py", "# background-color: #000000\nx = 1\n")
        assert _find_violations(path) == []

    def test_nao_bloqueia_cor_hexadecimal_css_com_alpha(self, tmp_path: Path) -> None:
        """Verifica o comportamento de nao bloqueia cor hexadecimal css com alpha."""
        path = _write(tmp_path, "a.py", "# background-color: #00000080\nx = 1\n")
        assert _find_violations(path) == []

    def test_bloqueia_issue_com_numero(self, tmp_path: Path) -> None:
        """Verifica o comportamento de bloqueia issue com numero."""
        path = _write(tmp_path, "a.py", "# fix para issue #318\nx = 1\n")
        assert len(_find_violations(path)) == 1

    def test_bloqueia_numero_de_issue_direto(self, tmp_path: Path) -> None:
        """Verifica o comportamento de bloqueia numero de issue direto."""
        path = _write(tmp_path, "a.ts", "// resolvido em #384\nconst x = 1;\n")
        assert len(_find_violations(path)) == 1
        path2 = _write(tmp_path, "b.py", "# ver #1 para contexto\nx = 1\n")
        assert len(_find_violations(path2)) == 1

    def test_bloqueia_pr_com_numero(self, tmp_path: Path) -> None:
        """Verifica o comportamento de bloqueia pr com numero."""
        path = _write(tmp_path, "a.tsx", "// PR 383 adicionou isso\nconst x = 1;\n")
        assert len(_find_violations(path)) == 1

    def test_bloqueia_pull_request(self, tmp_path: Path) -> None:
        """Verifica o comportamento de bloqueia pull request."""
        path = _write(tmp_path, "a.py", "# ver pull request #12\nx = 1\n")
        assert len(_find_violations(path)) == 1

    def test_bloqueia_pull_request_sem_numero(self, tmp_path: Path) -> None:
        """Verifica o comportamento de bloqueia pull request sem numero."""
        path = _write(tmp_path, "a.py", "# acompanhamento no pull request\nx = 1\n")
        assert len(_find_violations(path)) == 1

    def test_bloqueia_urls_github_de_issue_e_pull(self, tmp_path: Path) -> None:
        """Verifica o comportamento de bloqueia urls github de issue e pull."""
        issue = _write(
            tmp_path,
            "issue.py",
            "# https://github.com/vectora-ltda/vectora/issues/367\nx = 1\n",
        )
        pull = _write(
            tmp_path,
            "pull.py",
            "# https://github.com/vectora-ltda/vectora/pull/385\nx = 1\n",
        )
        assert len(_find_violations(issue)) == 1
        assert len(_find_violations(pull)) == 1

    def test_bloqueia_gha_e_github_actions(self, tmp_path: Path) -> None:
        """Verifica o comportamento de bloqueia gha e github actions."""
        path1 = _write(tmp_path, "b.py", "# rodado no GHA\nx = 1\n")
        assert len(_find_violations(path1)) == 1
        path2 = _write(tmp_path, "c.py", "# fluxo do github actions\nx = 1\n")
        assert len(_find_violations(path2)) == 1


class TestMain:
    def test_erro_borda_arquivo_limpo_retorna_zero(self, tmp_path: Path) -> None:
        """Verifica o comportamento de erro borda arquivo limpo retorna zero."""
        path = _write(tmp_path, "a.py", "def soma(a, b):\n    return a + b\n")
        assert main([path]) == 0

    def test_erro_borda_arquivo_com_violacao_retorna_um(self, tmp_path: Path) -> None:
        """Verifica o comportamento de erro borda arquivo com violacao retorna um."""
        path = _write(tmp_path, "a.py", "# Sprint 1\nx = 1\n")
        assert main([path]) == 1

    def test_erro_borda_arquivo_inexistente_nao_lanca_excecao(self) -> None:
        """Verifica o comportamento de erro borda arquivo inexistente nao lanca excecao."""
        assert main(["C:/caminho/que/nao/existe.py"]) == 0

    def test_erro_borda_multiplos_arquivos_um_so_com_violacao(
        self, tmp_path: Path
    ) -> None:
        """Verifica o comportamento de erro borda multiplos arquivos um so com violacao."""
        limpo = _write(tmp_path, "limpo.py", "x = 1\n")
        sujo = _write(tmp_path, "sujo.py", "# gerado com ChatGPT\nx = 1\n")
        assert main([limpo, sujo]) == 1
