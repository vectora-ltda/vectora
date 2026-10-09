"""Contratos reais de acesso ao GitHub com e sem credenciais.

Estes testes não usam mocks. Ficam fora da suíte padrão por causa do marker
``live`` e validam leitura pública sem token, leitura autenticada usando a
integração salva no Vectora e o CLI real do workspace.
"""

from __future__ import annotations

import os

import pytest

from backend.tools.context import ToolContext
from backend.tools.gh import gh_pr_view
from backend.tools.github import github_fetch_pr_diff
from backend.tools.web import fetch_url

pytestmark = [pytest.mark.live, pytest.mark.asyncio]


def _live_token() -> str:
    """Token fornecido somente pelo executor live; nunca lê arquivo ou imprime."""
    return (
        os.environ.get("VECTORA_LIVE_GITHUB_TOKEN", "").strip()
        or os.environ.get("GITHUB_TOKEN", "").strip()
    )


async def test_github_rest_pr_publico_sem_login_real(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A API REST deve ler o diff público sem GITHUB_TOKEN."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    result = await github_fetch_pr_diff(
        owner="vectora-ltda", repo="vectora", pr_number=319
    )
    assert '"status": "ok"' in result
    assert "diff --git" in result


async def test_github_rest_pr_com_token_real(monkeypatch: pytest.MonkeyPatch) -> None:
    """A API REST autentica a leitura do repositório principal."""
    token = _live_token()
    if not token:
        pytest.skip("defina VECTORA_LIVE_GITHUB_TOKEN para o teste autenticado")
    monkeypatch.setenv("GITHUB_TOKEN", token)
    result = await github_fetch_pr_diff(
        owner="vectora-ltda",
        repo="vectora",
        pr_number=319,
    )
    assert '"status": "ok"' in result
    assert "diff --git" in result


async def test_github_fetch_url_publico_sem_login_real(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rota web deve ler a issue pública sem token ou sessão GitHub."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    result = await fetch_url(url="https://github.com/vectora-ltda/vectora/issues/317")
    assert result.strip()
    assert not result.startswith("Error:")
    assert "vectora" in result.lower()


@pytest.mark.parametrize(
    ("repo_url", "marker"),
    [
        ("https://github.com/vectora-ltda/vectora-issues", "vectora-issues"),
        ("https://github.com/octocat/Hello-World", "hello-world"),
    ],
)
async def test_github_fetch_url_repos_publicos_sem_token_real(
    monkeypatch: pytest.MonkeyPatch, repo_url: str, marker: str
) -> None:
    """Repositórios públicos da organização e externos não exigem login."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    result = await fetch_url(url=repo_url)
    assert result.strip()
    assert not result.startswith("Error:")
    assert marker in result.lower()


async def test_github_fetch_url_com_token_real(monkeypatch):
    """O caminho web também envia o token autenticado sem expô-lo."""
    token = _live_token()
    if not token:
        pytest.skip("defina VECTORA_LIVE_GITHUB_TOKEN para o teste autenticado")
    monkeypatch.setenv("GITHUB_TOKEN", token)
    result = await fetch_url(
        url="https://github.com/vectora-ltda/vectora/issues/317",
    )
    assert result.strip()
    assert not result.startswith("Error:")
    assert "vectora" in result.lower()


async def test_github_cli_pr_view_real_com_sessao_do_terminal():
    """O caminho `gh` usa a sessão real já autenticada do terminal."""
    result = await gh_pr_view(
        ctx=ToolContext(workspace_id="", user_id="live-github-cli"),
        pr_number=319,
        workspace_id=None,
    )
    assert '"status": "ok"' in result
    assert '"number": 319' in result
