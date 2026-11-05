"""Tools do Linear para o agente.

Requer LINEAR_API_KEY no ambiente.
"""

from __future__ import annotations

import logging
import os

from backend.tools.registry import ToolExtras, vtool

logger = logging.getLogger(__name__)

_GRAPHQL = "https://api.linear.app/graphql"


def _headers() -> dict[str, str]:
    key = os.environ.get("LINEAR_API_KEY", "")
    if not key:
        raise RuntimeError("LINEAR_API_KEY não configurado. Adicione em Integrações.")
    return {"Authorization": key, "Content-Type": "application/json"}


@vtool(extras=ToolExtras(destructive=False, category="integrations", icon="list"))
async def linear_list_issues(
    team_key: str = "", state: str = "", limit: int = 20
) -> str:
    """Lista issues do Linear.

    Args:
        team_key: Chave do time (ex: 'ENG'). Vazio = todos os times.
        state: Estado das issues (ex: 'In Progress', 'Todo'). Vazio = todos.
        limit: Número máximo de issues (padrão 20).
    """
    try:
        import httpx

        filters: list[str] = []
        if team_key:
            filters.append(f'team: {{ key: {{ eq: "{team_key}" }} }}')
        if state:
            filters.append(f'state: {{ name: {{ eq: "{state}" }} }}')
        filter_str = f"filter: {{ {', '.join(filters)} }}" if filters else ""

        query = f"""
        query {{
          issues({filter_str} first: {min(limit, 50)}) {{
            nodes {{ id identifier title state {{ name }} priority assignee {{ name }} }}
          }}
        }}
        """
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(_GRAPHQL, json={"query": query}, headers=_headers())
        r.raise_for_status()
        issues = r.json().get("data", {}).get("issues", {}).get("nodes", [])
        if not issues:
            return "Nenhuma issue encontrada."
        lines = []
        for i in issues:
            assignee = i.get("assignee", {})
            assignee_name = assignee.get("name", "—") if assignee else "—"
            lines.append(
                f"[{i['identifier']}] {i['title']} — {i['state']['name']} — {assignee_name}"
            )
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("linear_list_issues error")
        return f"Erro ao listar issues Linear: {exc}"


@vtool(extras=ToolExtras(destructive=True, category="integrations", icon="plus-circle"))
async def linear_create_issue(
    title: str, team_key: str, description: str = "", priority: int = 0
) -> str:
    """Cria uma issue no Linear.

    Args:
        title: Título da issue.
        team_key: Chave do time (ex: 'ENG').
        description: Descrição em Markdown (opcional).
        priority: Prioridade (0=sem, 1=urgente, 2=alta, 3=média, 4=baixa).
    """
    try:
        import httpx

        # Resolve team ID a partir da key
        team_q = f'query {{ teams(filter: {{ key: {{ eq: "{team_key}" }} }}) {{ nodes {{ id }} }} }}'
        async with httpx.AsyncClient(timeout=10) as client:
            tr = await client.post(_GRAPHQL, json={"query": team_q}, headers=_headers())
        teams = tr.json().get("data", {}).get("teams", {}).get("nodes", [])
        if not teams:
            return f"Time '{team_key}' não encontrado."
        team_id = teams[0]["id"]

        mutation = """
        mutation CreateIssue($input: IssueCreateInput!) {
          issueCreate(input: $input) { issue { id identifier title } }
        }
        """
        variables = {
            "input": {
                "title": title,
                "teamId": team_id,
                "description": description,
                "priority": priority,
            }
        }
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                _GRAPHQL,
                json={"query": mutation, "variables": variables},
                headers=_headers(),
            )
        r.raise_for_status()
        issue = r.json().get("data", {}).get("issueCreate", {}).get("issue", {})
        if issue:
            return f"Issue criada: [{issue['identifier']}] {issue['title']}"
        return f"Erro ao criar issue: {r.json()}"
    except Exception as exc:
        logger.exception("linear_create_issue error title=%s", title)
        return f"Erro ao criar issue Linear: {exc}"


@vtool(extras=ToolExtras(destructive=True, category="integrations", icon="edit"))
async def linear_update_issue(
    issue_id: str, state_name: str = "", assignee_id: str = ""
) -> str:
    """Atualiza o estado ou assignee de uma issue no Linear.

    Args:
        issue_id: ID ou identificador da issue (ex: 'ENG-123').
        state_name: Nome do novo estado (ex: 'In Progress', 'Done').
        assignee_id: ID do usuário a atribuir (opcional).
    """
    try:
        import httpx

        # Resolve issue real ID se for identifier (ENG-123)
        if "-" in issue_id and not issue_id.startswith("id:"):
            q = f'query {{ issue(id: "{issue_id}") {{ id }} }}'
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(_GRAPHQL, json={"query": q}, headers=_headers())
            issue_id = r.json().get("data", {}).get("issue", {}).get("id", issue_id)

        update: dict = {}
        if state_name:
            # Busca o state ID
            sq = f'query {{ workflowStates(filter: {{ name: {{ eq: "{state_name}" }} }}) {{ nodes {{ id }} }} }}'
            async with httpx.AsyncClient(timeout=10) as client:
                sr = await client.post(_GRAPHQL, json={"query": sq}, headers=_headers())
            states = (
                sr.json().get("data", {}).get("workflowStates", {}).get("nodes", [])
            )
            if states:
                update["stateId"] = states[0]["id"]
        if assignee_id:
            update["assigneeId"] = assignee_id

        mutation = """
        mutation UpdateIssue($id: String!, $input: IssueUpdateInput!) {
          issueUpdate(id: $id, input: $input) { issue { id identifier state { name } } }
        }
        """
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                _GRAPHQL,
                json={
                    "query": mutation,
                    "variables": {"id": issue_id, "input": update},
                },
                headers=_headers(),
            )
        r.raise_for_status()
        updated = r.json().get("data", {}).get("issueUpdate", {}).get("issue", {})
        if updated:
            return f"Issue {updated.get('identifier', issue_id)} atualizada → {updated.get('state', {}).get('name', '?')}"
        return f"Erro: {r.json()}"
    except Exception as exc:
        logger.exception("linear_update_issue error id=%s", issue_id)
        return f"Erro ao atualizar issue Linear: {exc}"
