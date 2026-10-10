"""Exercita o dispatcher nativo nas rotas reais das Workbenches.

Os testes desta classe são deliberadamente ``live``: fazem HTTP contra o
GitHub e criam um subprocesso shell real. Eles não substituem o teste de
Electron/CDP, mas garantem que o caminho usado pelo agente (``ToolSpec`` e
``ainvoke``) não está quebrado antes de chegar à Workbench visual.
"""

from __future__ import annotations

import json
import os

import pytest

# O mesmo bootstrap usado pelo NativeAgent: resolve os módulos de tools e
# popula o registry antes de qualquer invocação.
from backend.nodes import tools as _native_tool_catalog
from backend.tools.context import ToolContext
from backend.tools.registry import TOOL_REGISTRY

pytestmark = [pytest.mark.live, pytest.mark.asyncio]


def _spec(name: str):
    spec = TOOL_REGISTRY.get(name)
    assert spec is not None, f"tool {name!r} não foi registrada"
    return spec


async def test_agent_dispatches_public_github_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A mesma chamada que o LLM faria lê um PR público sem credencial."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    result = await _spec("github_fetch_pr_diff").ainvoke(
        {"owner": "vectora-ltda", "repo": "vectora", "pr_number": 319},
        ToolContext(user_id="live-anonymous"),
    )
    payload = json.loads(result)
    assert payload["status"] == "ok"
    assert "diff --git" in payload["diff"]


async def test_agent_dispatches_authenticated_github_with_token() -> None:
    """A mesma rota usa o token configurado para um futuro repo privado.

    O job autenticado do workflow fornece ``GITHUB_TOKEN``. Falhar quando a
    variável não existe é intencional: este caso não pode virar um ``skip``
    silencioso e dar falsa confiança sobre o caminho autenticado.
    """
    token = os.getenv("VECTORA_LIVE_GITHUB_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token:
        pytest.fail("teste autenticado exige VECTORA_LIVE_GITHUB_TOKEN ou GITHUB_TOKEN")
    result = await _spec("github_fetch_pr_diff").ainvoke(
        {"owner": "vectora-ltda", "repo": "vectora", "pr_number": 319},
        ToolContext(user_id="live-authenticated"),
    )
    payload = json.loads(result)
    assert payload["status"] == "ok"
    assert "diff --git" in payload["diff"]


async def test_agent_dispatches_real_terminal_command() -> None:
    """A rota shell do agente executa um comando não interativo real."""
    command = "python -c \"print('vectora-live-tool-contract')\""
    result = await _spec("terminal").ainvoke(
        {"command": command},
        ToolContext(
            user_id="local",
            thread_id="live-tool-contract",
            permission_mode="bypass",
        ),
    )
    assert "vectora-live-tool-contract" in result


async def test_agent_dispatches_public_web_fetch_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A rota web também é invocada pelo schema nativo, sem mock HTTP."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    result = await _spec("fetch_url").ainvoke(
        {"url": "https://github.com/vectora-ltda/vectora-issues"},
        ToolContext(user_id="live-anonymous"),
    )
    assert not result.startswith("Error:")
    assert "vectora-issues" in result.lower()
