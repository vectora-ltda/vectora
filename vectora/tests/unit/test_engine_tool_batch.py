"""``execute_tool_batch`` — despacho paralelo/sequencial de tool calls do
mesmo turno, e o estágio de pós-processamento (`_apply_post_execute`) que
roda no resultado de toda tool antes de virar `VMessage`.

Cobertura era zero antes deste arquivo, apesar do módulo ser central ao
loop nativo (``conversation_loop.py`` chama ``execute_tool_batch`` em toda
volta que tem tool calls).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pydantic import create_model

from backend.engine.guardrails import LoopCapConfig, TurnBudget
from backend.engine.tool_batch import _redact_secrets, execute_tool_batch
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
