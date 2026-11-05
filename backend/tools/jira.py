"""Tools do Jira para o agente.

Requer JIRA_API_TOKEN, JIRA_BASE_URL e JIRA_EMAIL no ambiente.
"""

from __future__ import annotations

import base64
import logging
import os

from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)


def _auth() -> tuple[str, dict[str, str]]:
    token = os.environ.get("JIRA_API_TOKEN", "")
    email = os.environ.get("JIRA_EMAIL", "")
    base_url = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
    if not token or not email or not base_url:
        raise RuntimeError(
            "JIRA_API_TOKEN, JIRA_EMAIL e JIRA_BASE_URL são obrigatórios. Configure em Integrações."
        )
    cred = base64.b64encode(f"{email}:{token}".encode()).decode()
    return base_url, {
        "Authorization": f"Basic {cred}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


@vtool(extras=ToolExtras(destructive=False, category="integrations", icon="list"))
async def jira_list_issues(
    jql: str = "assignee = currentUser() ORDER BY updated DESC", max_results: int = 20
) -> str:
    """Lista issues do Jira usando JQL.

    Args:
        jql: Query JQL (ex: 'project = PROJ AND status = "In Progress"').
        max_results: Número máximo de issues (padrão 20).
    """
    try:
        import httpx

        base_url, headers = _auth()
        params = {
            "jql": jql,
            "maxResults": min(max_results, 50),
            "fields": "summary,status,assignee,priority,issuetype",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{base_url}/rest/api/3/search", params=params, headers=headers
            )
        r.raise_for_status()
        issues = r.json().get("issues", [])
        if not issues:
            return "Nenhuma issue encontrada."
        lines = []
        for i in issues:
            fields = i.get("fields", {})
            status = fields.get("status", {}).get("name", "?")
            assignee_data = fields.get("assignee")
            assignee = assignee_data.get("displayName", "—") if assignee_data else "—"
            lines.append(
                f"[{i['key']}] {fields.get('summary', '')} — {status} — {assignee}"
            )
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("jira_list_issues error")
        return f"Erro ao listar issues Jira: {exc}"


@vtool(extras=ToolExtras(destructive=True, category="integrations", icon="plus-circle"))
async def jira_create_issue(
    project_key: str, summary: str, description: str = "", issue_type: str = "Task"
) -> str:
    """Cria uma issue no Jira.

    Args:
        project_key: Chave do projeto (ex: 'PROJ').
        summary: Título da issue.
        description: Descrição em texto simples.
        issue_type: Tipo da issue (Task, Bug, Story, Epic).
    """
    try:
        import httpx

        base_url, headers = _auth()
        body = {
            "fields": {
                "project": {"key": project_key},
                "summary": summary,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {"type": "text", "text": description or summary}
                            ],
                        }
                    ],
                },
                "issuetype": {"name": issue_type},
            }
        }
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                f"{base_url}/rest/api/3/issue", json=body, headers=headers
            )
        r.raise_for_status()
        key = r.json().get("key", "?")
        return f"Issue criada: {key} — {summary}"
    except Exception as exc:
        logger.exception("jira_create_issue error project=%s", project_key)
        return f"Erro ao criar issue Jira: {exc}"


@vtool(extras=ToolExtras(destructive=True, category="integrations", icon="move"))
async def jira_transition(issue_key: str, transition_name: str) -> str:
    """Muda o status de uma issue do Jira.

    Args:
        issue_key: Chave da issue (ex: 'PROJ-42').
        transition_name: Nome da transição (ex: 'In Progress', 'Done').
    """
    try:
        import httpx

        base_url, headers = _auth()
        async with httpx.AsyncClient(timeout=10) as client:
            tr = await client.get(
                f"{base_url}/rest/api/3/issue/{issue_key}/transitions", headers=headers
            )
            tr.raise_for_status()
            transitions = tr.json().get("transitions", [])
            match = next(
                (
                    t
                    for t in transitions
                    if transition_name.lower() in t["name"].lower()
                ),
                None,
            )
            if not match:
                avail = ", ".join(t["name"] for t in transitions)
                return f"Transição '{transition_name}' não encontrada. Disponíveis: {avail}"

            r = await client.post(
                f"{base_url}/rest/api/3/issue/{issue_key}/transitions",
                json={"transition": {"id": match["id"]}},
                headers=headers,
            )
        if r.status_code in {200, 204}:
            return f"Issue {issue_key} movida para '{match['name']}'."
        return f"Erro ao transicionar: {r.status_code} — {r.text}"
    except Exception as exc:
        logger.exception("jira_transition error issue=%s", issue_key)
        return f"Erro ao transicionar issue Jira: {exc}"
