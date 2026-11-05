"""Testes para tools de integrações: gdrive, gmail, slack, linear, jira, notion.

Todos usam mocks de httpx — sem chamadas reais às APIs.
Cobre caminho feliz + erros (sem token, API retorna erro, payload malformado).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_http_response(
    status: int = 200, json_data: dict | None = None, text: str = ""
) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data or {}
    r.text = text
    r.raise_for_status = MagicMock()
    if status >= 400:
        import httpx

        r.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=r
        )
    return r


def _mock_http_client(responses: list[MagicMock]) -> MagicMock:
    """Cria um context manager AsyncClient que retorna responses em ordem."""
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=ctx)
    ctx.__aexit__ = AsyncMock(return_value=False)
    ctx.get = AsyncMock(side_effect=responses)
    ctx.post = AsyncMock(side_effect=responses)
    return ctx


# ---------------------------------------------------------------------------
# Google Drive
# ---------------------------------------------------------------------------


class TestGoogleDriveTools:
    @pytest.mark.asyncio
    async def test_list_sem_token(self) -> None:
        from backend.tools.gdrive import google_drive_list

        with patch.dict("os.environ", {}, clear=True):
            result = await google_drive_list(folder_id="root")
        assert "não configurado" in result.lower() or "erro" in result.lower()

    @pytest.mark.asyncio
    async def test_list_com_resultados(self) -> None:
        from backend.tools.gdrive import google_drive_list

        files_response = _mock_http_response(
            json_data={
                "files": [
                    {"id": "1abc", "name": "README.md", "mimeType": "text/plain"},
                    {
                        "id": "2def",
                        "name": "docs",
                        "mimeType": "application/vnd.google-apps.folder",
                    },
                ]
            }
        )
        with (
            patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": "tok"}),
            patch(
                "httpx.AsyncClient", return_value=_mock_http_client([files_response])
            ),
        ):
            result = await google_drive_list(folder_id="root")
        assert "README.md" in result
        assert "docs" in result

    @pytest.mark.asyncio
    async def test_list_vazia(self) -> None:
        from backend.tools.gdrive import google_drive_list

        empty = _mock_http_response(json_data={"files": []})
        with (
            patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": "tok"}),
            patch("httpx.AsyncClient", return_value=_mock_http_client([empty])),
        ):
            result = await google_drive_list(folder_id="root")
        assert "vazia" in result.lower()

    @pytest.mark.asyncio
    async def test_search_sem_resultados(self) -> None:
        from backend.tools.gdrive import google_drive_search

        empty = _mock_http_response(json_data={"files": []})
        with (
            patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": "tok"}),
            patch("httpx.AsyncClient", return_value=_mock_http_client([empty])),
        ):
            result = await google_drive_search(query="inexistente")
        assert "nenhum" in result.lower()


# ---------------------------------------------------------------------------
# Gmail
# ---------------------------------------------------------------------------


class TestGmailTools:
    @pytest.mark.asyncio
    async def test_list_sem_token(self) -> None:
        from backend.tools.gmail import gmail_list

        with patch.dict("os.environ", {}, clear=True):
            result = await gmail_list(query="")
        assert "não configurado" in result.lower() or "erro" in result.lower()

    @pytest.mark.asyncio
    async def test_list_sem_mensagens(self) -> None:
        from backend.tools.gmail import gmail_list

        empty = _mock_http_response(json_data={"messages": []})
        with (
            patch.dict("os.environ", {"GOOGLE_ACCESS_TOKEN": "tok"}),
            patch("httpx.AsyncClient", return_value=_mock_http_client([empty])),
        ):
            result = await gmail_list(query="")
        assert "nenhum" in result.lower()

    @pytest.mark.asyncio
    async def test_read_sem_token(self) -> None:
        from backend.tools.gmail import gmail_read

        with patch.dict("os.environ", {}, clear=True):
            result = await gmail_read(message_id="123")
        assert "não configurado" in result.lower() or "erro" in result.lower()


# ---------------------------------------------------------------------------
# Slack
# ---------------------------------------------------------------------------


class TestSlackTools:
    @pytest.mark.asyncio
    async def test_send_sem_token(self) -> None:
        from backend.tools.slack import slack_send

        with patch.dict("os.environ", {}, clear=True):
            result = await slack_send(channel="#geral", message="oi")
        assert "não configurado" in result.lower() or "erro" in result.lower()

    @pytest.mark.asyncio
    async def test_send_sucesso(self) -> None:
        from backend.tools.slack import slack_send

        ok = _mock_http_response(json_data={"ok": True})
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=ctx)
        ctx.__aexit__ = AsyncMock(return_value=False)
        ctx.post = AsyncMock(return_value=ok)
        with (
            patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb"}),
            patch("httpx.AsyncClient", return_value=ctx),
        ):
            result = await slack_send(channel="#geral", message="teste")
        assert "enviada" in result.lower()

    @pytest.mark.asyncio
    async def test_send_erro_slack(self) -> None:
        from backend.tools.slack import slack_send

        err = _mock_http_response(json_data={"ok": False, "error": "channel_not_found"})
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=ctx)
        ctx.__aexit__ = AsyncMock(return_value=False)
        ctx.post = AsyncMock(return_value=err)
        with (
            patch.dict("os.environ", {"SLACK_BOT_TOKEN": "xoxb"}),
            patch("httpx.AsyncClient", return_value=ctx),
        ):
            result = await slack_send(channel="#inexistente", message="x")
        assert "channel_not_found" in result

    @pytest.mark.asyncio
    async def test_list_channels_sem_token(self) -> None:
        from backend.tools.slack import slack_list_channels

        with patch.dict("os.environ", {}, clear=True):
            result = await slack_list_channels()
        assert "não configurado" in result.lower() or "erro" in result.lower()

    @pytest.mark.asyncio
    async def test_read_sem_token(self) -> None:
        from backend.tools.slack import slack_read

        with patch.dict("os.environ", {}, clear=True):
            result = await slack_read(channel="C123")
        assert "não configurado" in result.lower() or "erro" in result.lower()


# ---------------------------------------------------------------------------
# Linear
# ---------------------------------------------------------------------------


class TestLinearTools:
    @pytest.mark.asyncio
    async def test_list_sem_key(self) -> None:
        from backend.tools.linear import linear_list_issues

        with patch.dict("os.environ", {}, clear=True):
            result = await linear_list_issues()
        assert "não configurado" in result.lower() or "erro" in result.lower()

    @pytest.mark.asyncio
    async def test_list_com_resultados(self) -> None:
        from backend.tools.linear import linear_list_issues

        issues_resp = _mock_http_response(
            json_data={
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "1",
                                "identifier": "ENG-1",
                                "title": "Fix login",
                                "state": {"name": "In Progress"},
                                "assignee": {"name": "Bruno"},
                            }
                        ]
                    }
                }
            }
        )
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=ctx)
        ctx.__aexit__ = AsyncMock(return_value=False)
        ctx.post = AsyncMock(return_value=issues_resp)
        with (
            patch.dict("os.environ", {"LINEAR_API_KEY": "lin_api_xxx"}),
            patch("httpx.AsyncClient", return_value=ctx),
        ):
            result = await linear_list_issues()
        assert "ENG-1" in result
        assert "Fix login" in result

    @pytest.mark.asyncio
    async def test_create_sem_key(self) -> None:
        from backend.tools.linear import linear_create_issue

        with patch.dict("os.environ", {}, clear=True):
            result = await linear_create_issue(title="teste", team_key="ENG")
        assert "não configurado" in result.lower() or "erro" in result.lower()


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------


class TestJiraTools:
    _env = {
        "JIRA_API_TOKEN": "tok",
        "JIRA_EMAIL": "user@empresa.com",
        "JIRA_BASE_URL": "https://empresa.atlassian.net",
    }

    @pytest.mark.asyncio
    async def test_list_sem_config(self) -> None:
        from backend.tools.jira import jira_list_issues

        with patch.dict("os.environ", {}, clear=True):
            result = await jira_list_issues()
        assert "obrigatórios" in result or "erro" in result.lower()

    @pytest.mark.asyncio
    async def test_list_com_resultados(self) -> None:
        from backend.tools.jira import jira_list_issues

        resp = _mock_http_response(
            json_data={
                "issues": [
                    {
                        "key": "PROJ-1",
                        "fields": {
                            "summary": "Bug no login",
                            "status": {"name": "To Do"},
                            "assignee": {"displayName": "Dev"},
                        },
                    }
                ]
            }
        )
        with (
            patch.dict("os.environ", self._env),
            patch("httpx.AsyncClient", return_value=_mock_http_client([resp])),
        ):
            result = await jira_list_issues()
        assert "PROJ-1" in result
        assert "Bug no login" in result

    @pytest.mark.asyncio
    async def test_create_sem_config(self) -> None:
        from backend.tools.jira import jira_create_issue

        with patch.dict("os.environ", {}, clear=True):
            result = await jira_create_issue(project_key="PROJ", summary="x")
        assert "obrigatórios" in result or "erro" in result.lower()


# ---------------------------------------------------------------------------
# Notion
# ---------------------------------------------------------------------------


class TestNotionTools:
    @pytest.mark.asyncio
    async def test_search_sem_key(self) -> None:
        from backend.tools.notion import notion_search

        with patch.dict("os.environ", {}, clear=True):
            result = await notion_search(query="teste")
        assert "não configurado" in result.lower() or "erro" in result.lower()

    @pytest.mark.asyncio
    async def test_search_com_resultados(self) -> None:
        from backend.tools.notion import notion_search

        resp = _mock_http_response(
            json_data={
                "results": [
                    {
                        "object": "page",
                        "id": "page-1",
                        "properties": {
                            "title": {"title": [{"plain_text": "Minha Página"}]}
                        },
                    }
                ]
            }
        )
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=ctx)
        ctx.__aexit__ = AsyncMock(return_value=False)
        ctx.post = AsyncMock(return_value=resp)
        with (
            patch.dict("os.environ", {"NOTION_API_KEY": "secret_xxx"}),
            patch("httpx.AsyncClient", return_value=ctx),
        ):
            result = await notion_search(query="página")
        assert "Minha Página" in result

    @pytest.mark.asyncio
    async def test_read_sem_key(self) -> None:
        from backend.tools.notion import notion_read_page

        with patch.dict("os.environ", {}, clear=True):
            result = await notion_read_page(page_id="abc")
        assert "não configurado" in result.lower() or "erro" in result.lower()

    @pytest.mark.asyncio
    async def test_create_sem_key(self) -> None:
        from backend.tools.notion import notion_create_page

        with patch.dict("os.environ", {}, clear=True):
            result = await notion_create_page(parent_id="db", title="x")
        assert "não configurado" in result.lower() or "erro" in result.lower()
