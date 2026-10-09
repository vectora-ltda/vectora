"""GitHub tools — saída de API para o modelo de referência de webhook
(PR do GitHub → agente revisa e comenta).

Autenticação via ``GITHUB_TOKEN`` (mesmo token da integração OAuth/PAT em
``backend/api/handlers/oauth.py``, escopo ``repo`` já incluído).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

import httpx

from backend.tools.context import ToolContext
from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"


async def _github_token(ctx: ToolContext | None = None) -> str:
    """Resolve o token salvo na integração do usuário antes do ambiente global."""
    if ctx is not None:
        try:
            from backend.rbac.auth import get_env_overrides

            overrides = await get_env_overrides(ctx.user_id)
            token = overrides.get("GITHUB_TOKEN", "").strip()
            if token:
                return token
        except Exception:
            logger.debug(
                "github: não foi possível ler o token do usuário", exc_info=True
            )
    return os.environ.get("GITHUB_TOKEN", "").strip()


@vtool(
    extras=ToolExtras(
        render_hint="diff",
        category="github",
        destructive=False,
        icon="git-pull-request",
    )
)
async def github_fetch_pr_diff(
    owner: str, repo: str, pr_number: int, ctx: ToolContext | None = None
) -> str:
    """Busca o diff completo de um Pull Request via API do GitHub.

    O payload do webhook `pull_request` não traz o diff, só metadados —
    esta tool busca o conteúdo real para o agente revisar.

    Args:
        owner: dono do repositório (organização ou usuário).
        repo: nome do repositório.
        pr_number: número do PR.

    Returns:
        JSON com o diff em texto, ou um objeto JSON com status de erro.
    """
    url = f"{_GITHUB_API}/repos/{owner}/{repo}/pulls/{pr_number}"
    headers = {
        "Accept": "application/vnd.github.v3.diff",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = await _github_token(ctx)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        resp: httpx.Response | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(url, headers=headers)
                break
            except httpx.TransportError:
                if attempt == 2:
                    raise
                await asyncio.sleep(0.25 * (attempt + 1))
                logger.warning(
                    "github_fetch_pr_diff: transporte instável, repetindo",
                    extra={"owner": owner, "repo": repo, "pr_number": pr_number},
                )
        if resp is None:
            return json.dumps(
                {"status": "error", "error": "GitHub não retornou resposta."}
            )
        if resp.status_code != 200:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"GitHub API respondeu {resp.status_code}; verifique o repositório e o número do PR.",
                }
            )
        return json.dumps({"status": "ok", "diff": resp.text})
    except Exception as exc:
        logger.exception(
            "github_fetch_pr_diff: erro inesperado",
            extra={"owner": owner, "repo": repo, "pr_number": pr_number},
        )
        return json.dumps({"status": "error", "error": str(exc)})


@vtool(
    extras=ToolExtras(
        destructive=True,
        category="github",
        icon="message-square",
    )
)
async def github_post_pr_comment(
    owner: str,
    repo: str,
    pr_number: int,
    body: str,
    ctx: ToolContext | None = None,
) -> str:
    """Posta um comentário num Pull Request do GitHub.

    Usa o endpoint de comentários de Issues — PRs no GitHub são issues por
    baixo, mesmo endpoint dos dois.

    Args:
        owner: dono do repositório (organização ou usuário).
        repo: nome do repositório.
        pr_number: número do PR.
        body: texto do comentário (Markdown suportado pelo GitHub).

    Returns:
        JSON com a URL do comentário criado, ou um objeto JSON com status
        de erro.
    """
    token = await _github_token(ctx)
    if not token:
        return json.dumps({"status": "error", "error": "GITHUB_TOKEN não configurado."})

    url = f"{_GITHUB_API}/repos/{owner}/{repo}/issues/{pr_number}/comments"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json={"body": body})
        if resp.status_code != 201:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"GitHub API respondeu {resp.status_code}: {resp.text[:500]}",
                }
            )
        data = resp.json()
        return json.dumps({"status": "ok", "comment_url": data.get("html_url", "")})
    except Exception as exc:
        logger.exception(
            "github_post_pr_comment: erro inesperado",
            extra={"owner": owner, "repo": repo, "pr_number": pr_number},
        )
        return json.dumps({"status": "error", "error": str(exc)})
