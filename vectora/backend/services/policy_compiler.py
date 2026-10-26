"""Compilador de políticas e snapshot imutável para execução de ferramentas.

Garante governança estrita de execução:
- Validação unificada de allowlist/denylist de tools por usuário e escopo global.
- Enforcement de políticas de saída de rede (egress) e proteção SSRF.
- Controle de autoridade de servidores MCP.
- Restrições específicas para operações perigosas (git_push force, métodos mutáveis).
- Isolamento estrito para fluxos com conteúdo não confiável (untrusted_content).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from backend.engine.hitl import REQUIRE_APPROVAL
from backend.rbac import tool_policy
from backend.services.egress_policy import EgressPolicy, is_url_allowed
from backend.services.mcp_policy import evaluate as evaluate_mcp
from backend.tools.context import ToolContext

logger = logging.getLogger(__name__)

#: Conjunto canônico de ferramentas com potencial destrutivo (compatibilidade)
DESTRUCTIVE_TOOLS: frozenset[str] = REQUIRE_APPROVAL

# Navegação do Browser Workbench usa o mesmo perímetro de egress das tools
# web.  Manter a lista aqui evita que uma URL bloqueada passe pelo caminho CDP
# apenas porque não é uma chamada HTTP explícita.
_URL_EGRESS_TOOLS: frozenset[str] = frozenset(
    {
        "http_request",
        "web_fetch",
        "fetch_url",
        "web_crawl",
        "web_map",
        "browser_navigate",
        "browser_new_tab",
    }
)


@dataclass(frozen=True)
class PolicySnapshot:
    """Snapshot imutável e compilado de políticas aplicadas à execução de tools."""

    workspace_id: str | None = None
    user_id: str = "local"
    permission_mode: str = "ask"
    disabled_tools: frozenset[str] = field(default_factory=frozenset)
    egress_policy: EgressPolicy = field(default_factory=EgressPolicy)
    is_jailed: bool = False
    untrusted_content: bool = False
    allow_force_push: bool = False

    def is_tool_allowed(self, tool_name: str) -> tuple[bool, str]:
        """Valida se a tool pode ser chamada com base nas restrições da política."""
        if tool_name in self.disabled_tools:
            return False, f"Tool '{tool_name}' desabilitada pela política de permissões"

        if self.untrusted_content:
            from backend.engine.hitl import _tool_requires_approval

            if _tool_requires_approval(tool_name):
                return (
                    False,
                    f"Tool '{tool_name}' bloqueada para execução com conteúdo não confiável (untrusted_content)",
                )

        return True, ""

    def validate_call(  # noqa: PLR0911
        self, tool_name: str, args: dict[str, Any]
    ) -> tuple[bool, str]:
        """Validação estrita de pré-execução para argumentos e destinos de rede."""
        allowed, reason = self.is_tool_allowed(tool_name)
        if not allowed:
            return False, reason

        # Em fluxos com untrusted_content, bloqueia métodos HTTP mutáveis
        if self.untrusted_content and tool_name == "http_request":
            method = str(args.get("method", "GET")).upper()
            if method in ("POST", "PUT", "PATCH", "DELETE"):
                return (
                    False,
                    f"Método HTTP mutável '{method}' bloqueado para execução com conteúdo não confiável (untrusted_content)",
                )

        # Checagem de integridade de repositório para git_push
        if tool_name == "git_push":
            force_flag = bool(args.get("force", False))
            if force_flag and not self.allow_force_push:
                return (
                    False,
                    "git_push com --force bloqueado pela política de governança de repositório",
                )

        # Checagem unificada de saída de rede (egress)
        if tool_name in _URL_EGRESS_TOOLS:
            url = args.get("url")
            if isinstance(url, str):
                ok, egress_reason = is_url_allowed(url, self.egress_policy)
                if not ok:
                    return False, f"Egress policy violada para '{url}': {egress_reason}"

        # Checagem de conformidade de servidor MCP
        if tool_name == "call_mcp_tool":
            server_name = args.get("server_name") or args.get("server")
            if not server_name:
                inner_tool = args.get("tool_name")
                if isinstance(inner_tool, str):
                    try:
                        from backend.tools.mcp import _mcp_client

                        if _mcp_client is not None:
                            server_name = _mcp_client.tools_by_server().get(inner_tool)
                    except Exception:
                        server_name = None

            if not server_name:
                return (
                    False,
                    "Não foi possível resolver o servidor MCP para a ferramenta solicitada",
                )

            decision = evaluate_mcp(str(server_name), workspace_id=self.workspace_id)
            if not decision.allowed:
                return (
                    False,
                    f"Servidor MCP '{server_name}' bloqueado pela política de MCP ({decision.code})",
                )

        return True, ""


def compile_policy_snapshot(
    ctx: ToolContext,
    *,
    untrusted_content: bool | None = None,
    egress_policy: EgressPolicy | None = None,
    allow_force_push: bool = False,
) -> PolicySnapshot:
    """Compila o snapshot imutável de políticas para o contexto fornecido."""
    user_id = ctx.user_id or "local"
    mode = ctx.permission_mode or "ask"
    ws_id = ctx.workspace_id

    # 1. Obtenção de ferramentas desabilitadas via RBAC
    disabled = tool_policy.effective_disabled(user_id)

    # 2. Resolução da proveniência de conteúdo não confiável
    is_untrusted = untrusted_content
    if is_untrusted is None:
        extra = getattr(ctx, "_extra", {})
        if isinstance(extra, dict):
            is_untrusted = bool(extra.get("untrusted_content", False))
        else:
            is_untrusted = False

    # 3. Resolução de ambiente jailed
    is_jailed = False
    if ws_id:
        from backend.engine.hitl import _workspace_is_jailed

        is_jailed = _workspace_is_jailed(ws_id)

    eff_egress = egress_policy or EgressPolicy.for_workspace(ws_id)

    return PolicySnapshot(
        workspace_id=ws_id,
        user_id=user_id,
        permission_mode=mode,
        disabled_tools=frozenset(disabled),
        egress_policy=eff_egress,
        is_jailed=is_jailed,
        untrusted_content=bool(is_untrusted),
        allow_force_push=allow_force_push,
    )
