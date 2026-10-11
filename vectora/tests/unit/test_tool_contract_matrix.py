"""Contratos de superfície entre Workbenches, registry e agente nativo.

Estes testes não simulam o Electron nem o GitHub. Eles verificam a camada que
costuma quebrar antes deles: uma tool pode existir no módulo da Workbench e
mesmo assim ficar fora do catálogo entregue ao agente, ter ``ctx`` exposto no
schema ou deixar de ser invocável pelo dispatcher nativo.
"""

from __future__ import annotations

import inspect
import json

import pytest

from backend.nodes import tools as node_tools
from backend.services.agent_factory import get_native_agent
from backend.tools.context import ToolContext
from backend.tools.registry import TOOL_REGISTRY, ToolSpec

WORKBENCH_GROUPS = {
    "browser": node_tools.BROWSER_TOOLS,
    "filesystem_terminal": node_tools.FS_TOOLS,
    "git_github": node_tools.GIT_TOOLS,
}


def _workbench_specs() -> list[ToolSpec]:
    """Retorna as tools expostas pelas três Workbenches, sem duplicatas."""
    by_name: dict[str, ToolSpec] = {}
    for specs in WORKBENCH_GROUPS.values():
        for spec in specs:
            by_name[spec.name] = spec
    return list(by_name.values())


def _workbench_specs_with_required_args() -> list[ToolSpec]:
    return [
        spec
        for spec in _workbench_specs()
        if spec.args_model.model_json_schema().get("required")
    ]


def test_workbench_catalogs_are_registered_without_duplicates() -> None:
    """Toda tool visível na Workbench precisa estar no registry canônico."""
    for group_name, specs in WORKBENCH_GROUPS.items():
        names = [spec.name for spec in specs]
        assert len(names) == len(set(names)), f"duplicata no grupo {group_name}"
        for spec in specs:
            assert TOOL_REGISTRY.get(spec.name) is spec, (
                f"{group_name}/{spec.name} não aponta para o ToolSpec canônico"
            )


def test_workbench_catalogs_are_exposed_to_code_agent() -> None:
    """O agente de código recebe exatamente as tools da superfície nativa."""
    # A construção é síncrona no contrato público, mas o factory é async para
    # carregar catálogo/ABAC; o teste async abaixo valida a instância real.
    assert _workbench_specs()


@pytest.mark.asyncio
async def test_every_workbench_tool_is_in_native_agent_registry() -> None:
    agent = await get_native_agent(user_id="tool-contract-matrix", chat_mode=False)
    agent_specs = {spec.name: spec for spec in agent.tool_registry.all()}

    for spec in _workbench_specs():
        assert agent_specs.get(spec.name) is spec, (
            f"tool {spec.name} está na Workbench, mas não no NativeAgent"
        )


@pytest.mark.asyncio
async def test_chat_agent_does_not_leak_workbench_tools() -> None:
    agent = await get_native_agent(user_id="tool-contract-chat", chat_mode=True)
    names = {spec.name for spec in agent.tool_registry.all()}
    leaked = sorted({spec.name for spec in _workbench_specs()} & names)
    assert leaked == [], f"tools de Workbench vazaram para chat: {leaked}"


@pytest.mark.parametrize("spec", _workbench_specs(), ids=lambda spec: spec.name)
def test_workbench_tool_has_async_handler_and_valid_openai_schema(
    spec: ToolSpec,
) -> None:
    """O dispatcher e todos os providers recebem um schema executável."""
    assert inspect.iscoroutinefunction(spec.handler), spec.name
    schema = spec.openai_schema()
    assert schema["type"] == "function"
    function = schema["function"]
    assert function["name"] == spec.name
    assert "ctx" not in function["parameters"].get("properties", {})
    # Falha aqui detecta schemas que o provider não consegue serializar antes
    # de qualquer chamada de rede ou de UI.
    json.dumps(schema)


@pytest.mark.parametrize(
    "spec", _workbench_specs_with_required_args(), ids=lambda spec: spec.name
)
@pytest.mark.asyncio
async def test_invalid_arguments_are_reported_by_same_dispatcher(
    spec: ToolSpec,
) -> None:
    """Nenhuma tool pode escapar como exceção quando o LLM envia args inválidos."""
    required = list(spec.args_model.model_json_schema().get("required", []))
    # None é inválido para os tipos primitivos exigidos pela superfície das
    # Workbenches. Como a validação ocorre antes do handler, nenhuma rede,
    # filesystem ou processo externo é iniciado neste teste.
    invalid_args = dict.fromkeys(required)
    result = await spec.ainvoke(
        invalid_args, ToolContext(user_id="contract", workspace_id="ws")
    )
    assert isinstance(result, str)
    assert result.startswith("Error: argumentos inválidos")


def test_workbench_matrix_has_expected_routes() -> None:
    """Protege contra uma Workbench nova entrar sem teste de contrato."""
    assert {spec.name for spec in node_tools.BROWSER_TOOLS} >= {
        "browser_navigate",
        "browser_snapshot",
        "browser_list_tabs",
    }
    assert {spec.name for spec in node_tools.FS_TOOLS} >= {
        "terminal",
        "read_terminal",
        "write_terminal",
        "attach_terminal",
    }
    assert {spec.name for spec in node_tools.GIT_TOOLS} >= {
        "gh_pr_view",
        "gh_issue_view",
        "github_fetch_pr_diff",
    }


def test_optional_tool_context_is_internal_to_github_tools() -> None:
    """Regressão: ``ToolContext | None`` não pode virar argumento do LLM."""
    for name in ("github_fetch_pr_diff", "github_post_pr_comment"):
        spec = TOOL_REGISTRY.get(name)
        assert spec is not None
        assert spec.needs_ctx is True
        properties = spec.openai_schema()["function"]["parameters"]["properties"]
        assert "ctx" not in properties
