"""``execute_tool_batch`` — despacho paralelo/sequencial de tool calls do
mesmo turno, e o estágio de pós-processamento (`_apply_post_execute`) que
roda no resultado de toda tool antes de virar `VMessage`.

Cobertura era zero antes deste arquivo, apesar do módulo ser central ao
loop nativo (``conversation_loop.py`` chama ``execute_tool_batch`` em toda
volta que tem tool calls).
"""

from __future__ import annotations

import pytest

from backend.engine.guardrails import LoopCapConfig, TurnBudget
from backend.engine.tool_batch import _redact_secrets, execute_tool_batch
from backend.tools.context import ToolContext
from backend.tools.registry import TOOL_REGISTRY, ToolExtras, ToolRegistry, vtool
from backend.vtypes.message import ToolCall


def _register(registry: ToolRegistry, nome: str) -> None:
    spec = TOOL_REGISTRY.get(nome)
    assert spec is not None
    registry.register(spec)


@pytest.fixture
def ctx() -> ToolContext:
    return ToolContext(user_id="u1", thread_id="thread-1")


class TestDespachoParaleloSequencial:
    async def test_lote_sem_destrutiva_roda_em_paralelo(self, ctx):
        ordem: list[str] = []

        @vtool(extras=ToolExtras(destructive=False))
        async def tool_a(ctx: ToolContext) -> str:
            ordem.append("a")
            return "resultado a"

        @vtool(extras=ToolExtras(destructive=False))
        async def tool_b(ctx: ToolContext) -> str:
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

    async def test_lote_com_destrutiva_roda_sequencial_mas_executa_todas(self, ctx):
        """Erro/borda: uma única tool destrutiva no lote força o lote
        inteiro a rodar sequencial — mas nenhuma chamada é pulada."""

        @vtool(extras=ToolExtras(destructive=False))
        async def tool_leitura(ctx: ToolContext) -> str:
            return "lido"

        @vtool(extras=ToolExtras(destructive=True))
        async def tool_escrita(ctx: ToolContext) -> str:
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
        executada = False

        @vtool(extras=ToolExtras(destructive=False))
        async def tool_cara(ctx: ToolContext) -> str:
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
            return "OPENAI_API_KEY=sk-abcdefghijklmnopqrstuvwx"

        registry = ToolRegistry()
        _register(registry, "terminal_fake")

        calls = [ToolCall(id="c1", name="terminal_fake", args={})]
        resultados = await execute_tool_batch(calls, tool_registry=registry, ctx=ctx)

        assert "sk-abcdefghijklmnopqrstuvwx" not in resultados[0].text()
        assert "[REDACTED]" in resultados[0].text()
