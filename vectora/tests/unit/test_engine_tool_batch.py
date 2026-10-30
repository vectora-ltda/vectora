"""``execute_tool_batch`` — despacho paralelo/sequencial de tool calls do
mesmo turno, e o estágio de pós-processamento (`_apply_post_execute`) que
roda no resultado de toda tool antes de virar `VMessage`.

Cobertura era zero antes deste arquivo, apesar do módulo ser central ao
loop nativo (``conversation_loop.py`` chama ``execute_tool_batch`` em toda
volta que tem tool calls).
"""

from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest
from pydantic import create_model

from backend.engine.guardrails import LoopCapConfig, TurnBudget
from backend.engine.tool_batch import _redact_secrets, execute_tool_batch
from backend.llm.systemone.harness import SystemOneHarness
from backend.services.egress_policy import EgressPolicy
from backend.services.policy_compiler import PolicySnapshot
from backend.tools.context import ToolContext
from backend.tools.registry import (
    TOOL_REGISTRY,
    ToolExtras,
    ToolRegistry,
    ToolSpec,
    _result_is_error,
    vtool,
)
from backend.vtypes.message import ToolCall


@pytest.fixture(autouse=True)
def public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep browser-policy unit tests independent of external DNS."""
    original = socket.getaddrinfo

    def resolve(host, port, *args, **kwargs):
        if host == "example.com":
            return [
                (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))
            ]
        return original(host, port, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)


def _register(registry: ToolRegistry, nome: str) -> None:
    """Executa a operação register."""
    spec = TOOL_REGISTRY.get(nome)
    assert spec is not None
    registry.register(spec)


@pytest.fixture
def ctx() -> ToolContext:
    """Executa a operação ctx."""
    return ToolContext(user_id="u1", thread_id="thread-1")


class TestDespachoParaleloSequencial:
    @pytest.mark.parametrize(
        ("failure", "reason", "blocked"),
        [
            ("timeout", "decision_model_timeout", True),
            ("rate_limit", "decision_model_rate_limited", True),
            ("rejected", "decision_policy_rejected", True),
            ("empty", "decision_model_empty_response", True),
            ("low_confidence", "decision_model_missing_or_invalid_risk_score", True),
            ("invalid_score", "decision_model_missing_or_invalid_risk_score", True),
            ("malformed_response", "decision_model_invalid_response", True),
            ("unhealthy", "decision_model_unavailable", False),
        ],
    )
    async def test_risk_failure_never_executes_without_approval(
        self, failure: str, reason: str, blocked: bool
    ) -> None:
        """A started decision fails closed; an absent provider follows auto policy."""
        from backend.engine.hitl import (
            _risk_assessment_key,
            precompute_tool_risk_assessment,
            should_require_approval,
        )
        from backend.llm.systemone.errors import SystemOneRateLimitError
        from backend.llm.systemone.types import (
            ScoreAnswer,
            SystemOneMetadata,
            SystemOneResponse,
        )

        invoked = False

        async def mutating_tool(value: str, ctx: ToolContext) -> str:
            nonlocal invoked
            invoked = True
            assert value == "x"
            return "mutated"

        class RiskHarness:
            risk_assessment_timeout_s = 0.01
            confidence_threshold_hitl = 0.5

            async def is_ready(self) -> bool:
                return failure != "unhealthy"

            def set_mode(self, *_args: object, **_kwargs: object) -> None:
                pass

            async def assess_tool_risk(self, **_kwargs: object) -> object:
                if failure == "timeout":
                    await asyncio.sleep(0.05)
                if failure == "rate_limit":
                    raise SystemOneRateLimitError("rate limited")
                answer = (
                    ScoreAnswer(
                        score=1.5 if failure == "invalid_score" else 0.2,
                        confidence=0.1,
                    )
                    if failure
                    in {"low_confidence", "invalid_score", "malformed_response"}
                    else None
                )
                response = (
                    SystemOneResponse(
                        answers={"risk_score": answer},
                        metadata=SystemOneMetadata(
                            provider="clef-local", model="test", latency_ms=1
                        ),
                    )
                    if answer is not None
                    else None
                )
                if failure == "malformed_response":
                    response = SimpleNamespace(
                        answers={
                            "risk_score": SimpleNamespace(score=0.1, confidence=1.0)
                        }
                    )
                return SimpleNamespace(
                    mode="enforced",
                    response=response,
                    policy_result=SimpleNamespace(
                        outcome="rejected" if failure == "rejected" else "accepted",
                        requires_hitl=False,
                        reason="",
                    ),
                    provider="clef-local",
                    model="test",
                    policy_version="1",
                    decision_id="risk-decision",
                )

        ctx = ToolContext(
            user_id="u1",
            thread_id="risk-thread",
            workspace_id="risk-workspace",
            permission_mode="auto",
            _extra={
                "decision_model_preferences": {
                    "enabled": True,
                    "provider": "clef-local",
                    "surfaces": {"hitl": True},
                }
            },
        )
        call = ToolCall(id="risk-call", name="file_write", args={"value": "x"})
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="file_write",
                description="mutating",
                args_model=create_model("RiskArgs", value=(str, ...)),
                handler=mutating_tool,
                extras=ToolExtras(destructive=True),
                needs_ctx=True,
            )
        )

        elevated = await precompute_tool_risk_assessment(
            cast("SystemOneHarness", RiskHarness()),
            tool_name=call.name,
            ctx=ctx,
            args=call.args,
        )
        decision = ctx._extra["systemone_decisions"][
            _risk_assessment_key(call.name, call.args)
        ]
        assert elevated is blocked
        assert decision["reason"] == reason
        assert should_require_approval(call.name, ctx, call.args, []) is blocked

        result = await execute_tool_batch([call], tool_registry=registry, ctx=ctx)
        assert invoked is not blocked
        assert result[0].is_error is blocked

    async def test_disabled_surface_is_recorded_but_does_not_claim_model_protection(
        self,
    ) -> None:
        from backend.engine.hitl import (
            _risk_assessment_key,
            precompute_tool_risk_assessment,
            should_require_approval,
        )

        invoked = False

        async def read_like_tool(value: str, ctx: ToolContext) -> str:
            nonlocal invoked
            invoked = True
            return value

        class DisabledHarness:
            async def is_ready(self) -> bool:
                raise AssertionError("disabled surface must not probe a provider")

        ctx = ToolContext(
            user_id="u1",
            thread_id="disabled-surface-thread",
            run_id="disabled-surface-run",
            permission_mode="auto",
            _extra={
                "decision_model_preferences": {
                    "enabled": True,
                    "provider": "clef-local",
                    "surfaces": {"hitl": False},
                }
            },
        )
        call = ToolCall(
            id="disabled-surface-call", name="file_write", args={"value": "x"}
        )
        elevated = await precompute_tool_risk_assessment(
            cast("SystemOneHarness", DisabledHarness()),
            tool_name=call.name,
            ctx=ctx,
            args=call.args,
        )
        decision = ctx._extra["systemone_decisions"][
            _risk_assessment_key(call.name, call.args)
        ]

        assert elevated is False
        assert decision == {
            "decision": "unavailable",
            "reason": "decision_model_surface_disabled",
            "provider": "clef-local",
            "run_id": "disabled-surface-run",
            "thread_id": "disabled-surface-thread",
        }
        assert should_require_approval(call.name, ctx, call.args, []) is False

        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="file_write",
                description="test mutation",
                args_model=create_model("DisabledSurfaceArgs", value=(str, ...)),
                handler=read_like_tool,
                extras=ToolExtras(destructive=True),
                needs_ctx=True,
            )
        )
        result = await execute_tool_batch([call], tool_registry=registry, ctx=ctx)

        assert invoked is True
        assert result[0].is_error is False

    async def test_revocation_during_risk_assessment_blocks_tool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A preference change while the provider runs invalidates its allow."""
        from backend.engine.hitl import (
            _risk_assessment_key,
            precompute_tool_risk_assessment,
            should_require_approval,
        )
        from backend.llm.systemone.settings import SystemOneSettings
        from backend.services.policy_compiler import compile_policy_snapshot
        from backend.settings import settings

        preferences: dict[str, object] = {
            "decisionModelsEnabled": True,
            "decisionModelProvider": "clef-local",
            "decisionModelSurfaces": {"hitl": True},
        }

        class PreferenceStore:
            def get_frontend_prefs(self, _user_id: str, _workspace_id: str | None):
                return dict(preferences)

        monkeypatch.setattr(
            "backend.workspace.runtime_settings.runtime_settings", PreferenceStore()
        )
        invoked = False

        async def mutating_tool(value: str, ctx: ToolContext) -> str:
            nonlocal invoked
            invoked = True
            return value

        class RevokingHarness:
            risk_assessment_timeout_s = SystemOneSettings().risk_assessment_timeout_s
            confidence_threshold_hitl = settings.systemone_confidence_threshold_hitl

            async def is_ready(self) -> bool:
                return True

            def set_mode(self, *_args: object, **_kwargs: object) -> None:
                pass

            async def assess_tool_risk(self, **_kwargs: object) -> object:
                preferences["decisionModelsEnabled"] = False
                return SimpleNamespace(
                    mode="enforced",
                    response=SimpleNamespace(
                        answers={
                            "risk_score": SimpleNamespace(score=0.1, confidence=1.0)
                        }
                    ),
                    policy_result=SimpleNamespace(
                        outcome="accepted", requires_hitl=False, reason=""
                    ),
                    provider="clef-local",
                    model="test",
                    policy_version="1",
                    decision_id="revoked-decision",
                )

        ctx = ToolContext(
            user_id="u1",
            thread_id="revocation-thread",
            workspace_id="revocation-workspace",
            run_id="revocation-run",
            permission_mode="auto",
            _extra={
                "decision_model_preferences": {
                    "enabled": True,
                    "provider": "clef-local",
                    "surfaces": {"hitl": True},
                },
            },
        )
        from backend.llm.systemone.settings import SystemOneSettings

        ctx._extra["decision_model_snapshot"] = {
            "run_id": ctx.run_id,
            "preferences": {
                "enabled": True,
                "provider": "clef-local",
                "surfaces": {"hitl": True},
            },
            "model": settings.systemone_local_model,
            "systemone_enabled": settings.systemone_enabled,
            "allow_remote_fallback": settings.systemone_allow_remote_fallback,
            "risk_assessment_timeout_s": SystemOneSettings().risk_assessment_timeout_s,
            "confidence_threshold_hitl": settings.systemone_confidence_threshold_hitl,
            "policy_version": compile_policy_snapshot(ctx).version,
        }
        call = ToolCall(id="revoked-call", name="file_write", args={"value": "x"})
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="file_write",
                description="mutating",
                args_model=create_model("RevocationArgs", value=(str, ...)),
                handler=mutating_tool,
                extras=ToolExtras(destructive=True),
                needs_ctx=True,
            )
        )

        await precompute_tool_risk_assessment(
            cast("SystemOneHarness", RevokingHarness()),
            tool_name=call.name,
            ctx=ctx,
            args=call.args,
        )
        key = _risk_assessment_key(call.name, call.args)
        assert ctx._extra["systemone_decisions"][key]["reason"] == (
            "decision_model_revoked"
        )
        assert ctx._extra["systemone_decisions"][key]["decision"] == "requires_human"
        assert should_require_approval(call.name, ctx, call.args, []) is True

        result = await execute_tool_batch([call], tool_registry=registry, ctx=ctx)
        assert invoked is False
        assert result[0].is_error is True

    async def test_revocation_before_risk_assessment_blocks_tool(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Disabling a model after run start still requires human approval."""
        from backend.engine.hitl import (
            _risk_assessment_key,
            precompute_tool_risk_assessment,
            should_require_approval,
        )
        from backend.llm.systemone.settings import SystemOneSettings
        from backend.services.policy_compiler import compile_policy_snapshot
        from backend.settings import settings

        class PreferenceStore:
            def get_frontend_prefs(self, _user_id: str, _workspace_id: str | None):
                return {
                    "decisionModelsEnabled": False,
                    "decisionModelProvider": "clef-local",
                    "decisionModelSurfaces": {"hitl": True},
                }

        monkeypatch.setattr(
            "backend.workspace.runtime_settings.runtime_settings", PreferenceStore()
        )
        ctx = ToolContext(
            user_id="u1",
            thread_id="revocation-before-thread",
            workspace_id="revocation-before-workspace",
            run_id="revocation-before-run",
            permission_mode="auto",
            _extra={
                "decision_model_preferences": {
                    "enabled": True,
                    "provider": "clef-local",
                    "surfaces": {"hitl": True},
                },
            },
        )
        ctx._extra["decision_model_snapshot"] = {
            "run_id": ctx.run_id,
            "preferences": {
                "enabled": True,
                "provider": "clef-local",
                "surfaces": {"hitl": True},
            },
            "model": settings.systemone_local_model,
            "systemone_enabled": settings.systemone_enabled,
            "allow_remote_fallback": settings.systemone_allow_remote_fallback,
            "risk_assessment_timeout_s": SystemOneSettings().risk_assessment_timeout_s,
            "confidence_threshold_hitl": settings.systemone_confidence_threshold_hitl,
            "policy_version": compile_policy_snapshot(ctx).version,
        }
        call = ToolCall(
            id="revoked-before-call", name="file_write", args={"value": "x"}
        )

        class NeverCalledHarness:
            async def is_ready(self) -> bool:
                raise AssertionError("revoked decision must not probe the provider")

        await precompute_tool_risk_assessment(
            cast("SystemOneHarness", NeverCalledHarness()),
            tool_name=call.name,
            ctx=ctx,
            args=call.args,
        )

        decision = ctx._extra["systemone_decisions"][
            _risk_assessment_key(call.name, call.args)
        ]
        assert decision["decision"] == "requires_human"
        assert decision["reason"] == "decision_model_revoked"
        assert should_require_approval(call.name, ctx, call.args, []) is True

    async def test_veto_do_decision_model_impede_handler(
        self, ctx: ToolContext
    ) -> None:
        """An explicit deny blocks the tool before its handler can cause effects."""
        invoked = False

        async def mutating_tool(ctx: ToolContext) -> str:
            nonlocal invoked
            invoked = True
            return "mutated"

        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="mutating_tool",
                description="mutating",
                args_model=create_model("MutatingArgs", value=(str, ...)),
                handler=mutating_tool,
                extras=ToolExtras(destructive=True),
                needs_ctx=True,
            )
        )
        from backend.engine.hitl import _risk_assessment_key

        call = ToolCall(id="deny-1", name="mutating_tool", args={"value": "x"})
        ctx._extra["systemone_decisions"] = {
            _risk_assessment_key(call.name, call.args): {
                "decision": "deny",
                "reason": "high_risk",
            }
        }

        result = await execute_tool_batch([call], tool_registry=registry, ctx=ctx)

        assert invoked is False
        assert result[0].is_error is True
        assert "decision_model_denied" in result[0].text()

    def test_json_recursivo_nao_classifica_resultado_como_erro(self) -> None:
        """JSON profundamente aninhado não pode quebrar a classificação da tool."""
        raw = "[" * 5000 + "0" + "]" * 5000
        assert _result_is_error(raw) is False

    async def test_json_recursivo_preserva_texto_e_is_error_falso(
        self, ctx: ToolContext
    ) -> None:
        """O lote preserva o texto original quando a classificação recursiva falha."""
        raw = "[" * 5000 + "0" + "]" * 5000

        async def deeply_nested(ctx: ToolContext) -> str:
            """Return a deeply nested raw value for envelope preservation."""
            return raw

        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="deeply_nested",
                description="deeply nested result",
                args_model=create_model("DeeplyNestedArgs"),
                handler=deeply_nested,
                extras=ToolExtras(),
                needs_ctx=True,
            )
        )
        result = await execute_tool_batch(
            [ToolCall(id="deep", name="deeply_nested", args={})],
            tool_registry=registry,
            ctx=ctx,
        )

        assert result[0].text() == raw
        assert result[0].is_error is False

    async def test_browser_redirect_blocked_is_error_in_batch(
        self, ctx: ToolContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Envelopes JSON de browser bloqueado preservam ``is_error``."""
        from backend.tools import browser_devtools

        monkeypatch.setattr(
            browser_devtools, "new_tab", AsyncMock(return_value="tab-1")
        )
        monkeypatch.setattr(
            browser_devtools,
            "list_tabs",
            AsyncMock(
                return_value=[{"tab_id": "tab-1", "url": "http://127.0.0.1/private"}]
            ),
        )
        monkeypatch.setattr(browser_devtools, "close_tab", AsyncMock(return_value=True))
        registry = ToolRegistry()
        spec = TOOL_REGISTRY.get("browser_new_tab")
        assert spec is not None
        registry.register(spec)

        results = await execute_tool_batch(
            [
                ToolCall(
                    id="redirect-blocked",
                    name="browser_new_tab",
                    args={"url": "https://example.com"},
                )
            ],
            tool_registry=registry,
            ctx=ctx,
        )

        assert results[0].is_error is True
        assert "redirect bloqueado" in results[0].text()

    async def test_egress_bloqueada_nao_invoca_handler(self, ctx: ToolContext) -> None:
        """A policy deve impedir o handler, não apenas marcar sua resposta."""
        executada = False

        async def browser_navigate_fake(url: str, ctx: ToolContext) -> str:
            nonlocal executada
            executada = True
            return "nunca deveria executar"

        registry = ToolRegistry()
        spec = ToolSpec(
            name="browser_navigate",
            description="fake browser navigation",
            args_model=create_model("BrowserNavigateArgs", url=(str, ...)),
            handler=browser_navigate_fake,
            extras=ToolExtras(destructive=False, category="browser"),
            needs_ctx=True,
        )
        registry.register(spec)
        snapshot = PolicySnapshot(
            egress_policy=EgressPolicy(allowed_hosts=["allowed.com"])
        )

        result = await execute_tool_batch(
            [
                ToolCall(
                    id="blocked-browser",
                    name="browser_navigate",
                    args={"url": "https://forbidden.com"},
                )
            ],
            tool_registry=registry,
            ctx=ctx,
            policy_snapshot=snapshot,
        )

        assert executada is False
        assert result[0].is_error is True
        assert "policy_denied" in result[0].text()

    async def test_falha_na_notificacao_de_inicio_nao_interrompe_a_tool(
        self, ctx: ToolContext
    ) -> None:
        """Telemetria indisponível não pode transformar uma execução válida em erro."""

        executada = False

        @vtool(extras=ToolExtras(destructive=False))
        async def tool_resiliente(ctx: ToolContext) -> str:
            """Executa a operação tool resiliente."""
            nonlocal executada
            executada = True
            return "resultado"

        registry = ToolRegistry()
        _register(registry, "tool_resiliente")

        async def falhar_ao_notificar(_call: ToolCall) -> None:
            """Executa a operação falhar ao notificar."""
            raise RuntimeError("sink indisponível")

        resultados = await execute_tool_batch(
            [ToolCall(id="c1", name="tool_resiliente", args={})],
            tool_registry=registry,
            ctx=ctx,
            on_tool_started=falhar_ao_notificar,
        )

        assert executada is True
        assert resultados[0].is_error is False
        assert resultados[0].text() == "resultado"

    async def test_lote_sem_destrutiva_roda_em_paralelo(self, ctx: ToolContext) -> None:
        """Verifica o comportamento de lote sem destrutiva roda em paralelo."""
        ordem: list[str] = []

        @vtool(extras=ToolExtras(destructive=False))
        async def tool_a(ctx: ToolContext) -> str:
            """Executa a operação tool a."""
            ordem.append("a")
            return "resultado a"

        @vtool(extras=ToolExtras(destructive=False))
        async def tool_b(ctx: ToolContext) -> str:
            """Executa a operação tool b."""
            ordem.append("b")
            return "resultado b"

        registry = ToolRegistry()
        _register(registry, "tool_a")
        _register(registry, "tool_b")

        calls = [
            ToolCall(id="c1", name="tool_a", args={}),
            ToolCall(id="c2", name="tool_b", args={}),
        ]
        resultados = await execute_tool_batch(calls, tool_registry=registry, ctx=ctx)

        assert [r.text() for r in resultados] == ["resultado a", "resultado b"]
        assert set(ordem) == {"a", "b"}

    async def test_lote_com_destrutiva_roda_sequencial_mas_executa_todas(
        self, ctx: ToolContext
    ) -> None:
        """Erro/borda: uma única tool destrutiva no lote força o lote
        inteiro a rodar sequencial — mas nenhuma chamada é pulada."""

        @vtool(extras=ToolExtras(destructive=False))
        async def tool_leitura(ctx: ToolContext) -> str:
            """Executa a operação tool leitura."""
            return "lido"

        @vtool(extras=ToolExtras(destructive=True))
        async def tool_escrita(ctx: ToolContext) -> str:
            """Executa a operação tool escrita."""
            return "escrito"

        registry = ToolRegistry()
        _register(registry, "tool_leitura")
        _register(registry, "tool_escrita")

        calls = [
            ToolCall(id="c1", name="tool_leitura", args={}),
            ToolCall(id="c2", name="tool_escrita", args={}),
        ]
        resultados = await execute_tool_batch(calls, tool_registry=registry, ctx=ctx)

        assert [r.text() for r in resultados] == ["lido", "escrito"]

    async def test_tool_inexistente_no_registry_vira_erro_tipado(self, ctx):
        """Verifica o comportamento de tool inexistente no registry vira erro tipado."""
        calls = [ToolCall(id="c1", name="nao-existe", args={})]
        resultados = await execute_tool_batch(
            calls, tool_registry=ToolRegistry(), ctx=ctx
        )

        assert len(resultados) == 1
        assert resultados[0].is_error is True
        assert "não encontrada" in resultados[0].text()

    async def test_falha_localizada_de_tool_mcp_vira_erro_tipado(self, ctx):
        """Mensagens MCP em português devem persistir como erro."""

        @vtool(extras=ToolExtras(destructive=False))
        async def mcp_bloqueada(ctx: ToolContext) -> str:
            """Executa a operação mcp bloqueada."""
            return "Erro ao invocar tool MCP 'mcp_bloqueada': servidor indisponível."

        registry = ToolRegistry()
        spec = TOOL_REGISTRY.get("mcp_bloqueada")
        assert spec is not None
        registry.register(spec)
        resultados = await execute_tool_batch(
            [ToolCall(id="c1", name="mcp_bloqueada", args={})],
            tool_registry=registry,
            ctx=ctx,
        )

        assert resultados[0].is_error is True


class TestTurnBudget:
    async def test_chamada_alem_do_teto_vira_erro_sem_executar(self, ctx):
        """Verifica o comportamento de chamada alem do teto vira erro sem executar."""
        executada = False

        @vtool(extras=ToolExtras(destructive=False))
        async def tool_cara(ctx: ToolContext) -> str:
            """Executa a operação tool cara."""
            nonlocal executada
            executada = True
            return "ok"

        registry = ToolRegistry()
        _register(registry, "tool_cara")

        budget = TurnBudget(config=LoopCapConfig(max_tool_calls_per_turn=0))
        calls = [ToolCall(id="c1", name="tool_cara", args={})]

        resultados = await execute_tool_batch(
            calls, tool_registry=registry, ctx=ctx, turn_budget=budget
        )

        assert resultados[0].is_error is True
        assert "guardrail" in resultados[0].text()
        assert executada is False


class TestRedacaoDeSegredosPosExecucao:
    """Achado de auditoria (comparação com o estágio `tools/post-execute`
    do deepseek-harness): a saída de uma tool pode vazar segredos reais
    (variável de ambiente ecoada por `terminal`, chave colada num arquivo
    lido por `file_read`) — sem um estágio de pós-processamento, esse
    conteúdo ia direto pro histórico persistido e pro LLM."""

    def test_redact_secrets_mascara_padroes_conhecidos(self):
        """Verifica o comportamento de redact secrets mascara padroes conhecidos."""
        texto = (
            "export OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwx\n"
            "github token: ghp_" + "a" * 36 + "\n"
            "aws key: AKIAABCDEFGHIJKLMNOP\n"
            "texto normal sem segredo nenhum"
        )

        resultado = _redact_secrets(texto)

        assert "sk-abcdefghijklmnopqrstuvwx" not in resultado
        assert "ghp_" + "a" * 36 not in resultado
        assert "AKIAABCDEFGHIJKLMNOP" not in resultado
        assert resultado.count("[REDACTED]") == 3
        assert "texto normal sem segredo nenhum" in resultado

    def test_redact_secrets_texto_sem_segredo_fica_intacto(self):
        """Erro/borda: texto sem nenhum padrão de segredo não é alterado
        (a expressão regular não pode ser gulosa a ponto de mascarar
        conteúdo normal)."""
        texto = "sk- sozinho, ou ghp_curto, ou AKIA123 não batem o padrão"

        assert _redact_secrets(texto) == texto

    async def test_segredo_vazado_por_tool_e_redigido_antes_de_virar_mensagem(
        self, ctx
    ):
        """Fim a fim: uma tool que "vaza" uma chave na saída (ex.:
        `terminal` ecoando uma env var) tem o resultado redigido pelo
        pipeline real (`execute_tool_batch` → `_apply_post_execute`), não
        só a função de redação isolada."""

        @vtool(extras=ToolExtras(destructive=False))
        async def terminal_fake(ctx: ToolContext) -> str:
            """Executa a operação terminal fake."""
            return "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwx"

        registry = ToolRegistry()
        _register(registry, "terminal_fake")

        calls = [ToolCall(id="c1", name="terminal_fake", args={})]
        resultados = await execute_tool_batch(calls, tool_registry=registry, ctx=ctx)

        assert "sk-abcdefghijklmnopqrstuvwx" not in resultados[0].text()
        assert "[REDACTED]" in resultados[0].text()

    async def test_validacao_de_policy_nao_bloqueia_o_event_loop(self, ctx):
        """Uma validação lenta (DNS síncrono) roda fora do loop: outras tarefas
        continuam avançando, e a negação ainda impede a tool de executar."""
        import time

        ran: list[str] = []

        @vtool(extras=ToolExtras(destructive=False))
        async def slow_policy_probe(ctx: ToolContext) -> str:
            ran.append("executou")
            return "ok"

        registry = ToolRegistry()
        _register(registry, "slow_policy_probe")

        class _SlowSnapshot:
            def __init__(self, verdict: bool) -> None:
                self.verdict = verdict

            def validate_call(self, name: str, args: dict) -> tuple[bool, str]:
                time.sleep(0.3)
                return self.verdict, "" if self.verdict else "fora da allowlist"

        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(ticker())
        try:
            allowed = await execute_tool_batch(
                [ToolCall(id="slow-ok", name="slow_policy_probe", args={})],
                tool_registry=registry,
                ctx=ctx,
                policy_snapshot=cast("PolicySnapshot", _SlowSnapshot(True)),
            )
            ticks_while_validating = ticks
            denied = await execute_tool_batch(
                [ToolCall(id="slow-no", name="slow_policy_probe", args={})],
                tool_registry=registry,
                ctx=ctx,
                policy_snapshot=cast("PolicySnapshot", _SlowSnapshot(False)),
            )
        finally:
            task.cancel()

        assert allowed[0].text() == "ok"
        assert ticks_while_validating >= 10
        assert "policy_denied" in denied[0].text()
        assert ran == ["executou"]

    async def test_propagates_immutable_policy_snapshot_to_tool_context(self, ctx):
        """A tool recebe o snapshot do run, sem recompilar settings mutáveis."""
        seen: list[object] = []

        @vtool(extras=ToolExtras(destructive=False))
        async def snapshot_probe(ctx: ToolContext) -> str:
            seen.append(ctx._extra.get("policy_snapshot"))
            return "ok"

        registry = ToolRegistry()
        _register(registry, "snapshot_probe")
        snapshot = PolicySnapshot(
            workspace_id="ws-policy",
            egress_policy=EgressPolicy(allowed_hosts=["example.com"]),
        )

        results = await execute_tool_batch(
            [ToolCall(id="snapshot", name="snapshot_probe", args={})],
            tool_registry=registry,
            ctx=ctx,
            policy_snapshot=snapshot,
        )

        assert results[0].text() == "ok"
        assert seen == [snapshot]


@pytest.mark.parametrize("status", ["conflict", "not_git"])
def test_envelopes_git_sem_sucesso_contam_como_erro(status: str) -> None:
    """Par de erro: conflito de merge e workspace sem Git não são resultados
    bem-sucedidos; o lote precisa marcá-los como erro."""
    from backend.engine.tool_batch import _is_tool_error

    assert _is_tool_error(f'{{"status":"{status}"}}', structured=True) is True
    assert _is_tool_error('{"status":"ok"}', structured=True) is False
