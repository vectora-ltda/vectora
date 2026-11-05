"""Testes para backend/engine/guardrails.py — LoopCapConfig/TurnBudget."""

from __future__ import annotations

from backend.engine.guardrails import LoopCapConfig, TurnBudget
from backend.tools.context import ToolContext
from backend.tools.registry import TOOL_REGISTRY, ToolExtras, ToolSpec, vtool


def _make_spec(category: str) -> ToolSpec:
    """`ToolSpec` real (não fake solto) via `@vtool` — registra e
    imediatamente remove do `TOOL_REGISTRY` global, pra não colidir com
    outros testes que também usam nomes de tool locais."""
    nome = f"dummy_{category}"

    async def _dummy(ctx: ToolContext) -> str:
        return "ok"

    _dummy.__name__ = nome
    vtool(extras=ToolExtras(category=category))(_dummy)

    found = TOOL_REGISTRY.get(nome)
    assert found is not None
    TOOL_REGISTRY._tools.pop(nome, None)
    return found


class TestRecordToolCall:
    def test_dentro_do_teto_incrementa_e_devolve_none(self):
        budget = TurnBudget(config=LoopCapConfig(max_tool_calls_per_turn=3))
        assert budget.record_tool_call(None) is None
        assert budget.record_tool_call(None) is None
        assert budget.tool_calls == 2
        assert budget.exceeded is None

    def test_estoura_teto_de_tool_calls_bloqueia_e_trava(self):
        budget = TurnBudget(config=LoopCapConfig(max_tool_calls_per_turn=1))
        assert budget.record_tool_call(None) is None
        assert budget.record_tool_call(None) == "max_tool_calls_per_turn"
        assert budget.exceeded == "max_tool_calls_per_turn"
        # Travado: chamada seguinte devolve o mesmo código sem reavaliar.
        assert budget.record_tool_call(None) == "max_tool_calls_per_turn"
        assert budget.tool_calls == 1

    def test_categoria_de_rede_conta_pro_teto_dedicado(self):
        budget = TurnBudget(config=LoopCapConfig(max_network_calls_per_turn=1))
        spec_rede = _make_spec("web")
        assert budget.record_tool_call(spec_rede) is None
        assert budget.network_calls == 1
        assert budget.record_tool_call(spec_rede) == "max_network_calls_per_turn"

    def test_tool_sem_categoria_de_rede_nao_conta_pro_teto_de_rede(self):
        budget = TurnBudget(config=LoopCapConfig(max_network_calls_per_turn=0))
        spec_local = _make_spec("filesystem")
        assert budget.record_tool_call(spec_local) is None
        assert budget.network_calls == 0

    def test_teto_none_desliga_a_dimensao(self):
        budget = TurnBudget(
            config=LoopCapConfig(
                max_tool_calls_per_turn=None, max_network_calls_per_turn=None
            )
        )
        spec_rede = _make_spec("browser")
        for _ in range(50):
            assert budget.record_tool_call(spec_rede) is None
        assert budget.exceeded is None


class TestRecordSubagentSpawn:
    def test_dentro_do_teto_incrementa(self):
        budget = TurnBudget(config=LoopCapConfig(max_subagent_spawns_per_turn=2))
        assert budget.record_subagent_spawn() is None
        assert budget.record_subagent_spawn() is None
        assert budget.subagent_spawns == 2

    def test_estoura_teto_bloqueia_spawn(self):
        budget = TurnBudget(config=LoopCapConfig(max_subagent_spawns_per_turn=0))
        assert budget.record_subagent_spawn() == "max_subagent_spawns_per_turn"
        assert budget.subagent_spawns == 0


class TestRecordAitlCall:
    def test_dentro_do_teto_incrementa(self):
        budget = TurnBudget(config=LoopCapConfig(max_aitl_calls_per_turn=1))
        assert budget.record_aitl_call() is None
        assert budget.aitl_calls == 1

    def test_estoura_teto_bloqueia_chamada(self):
        budget = TurnBudget(config=LoopCapConfig(max_aitl_calls_per_turn=0))
        assert budget.record_aitl_call() == "max_aitl_calls_per_turn"


class TestExceededTravaTodasAsDimensoes:
    def test_exceeded_de_uma_dimensao_bloqueia_as_demais(self):
        """Uma vez travado, nenhum record_* de nenhuma dimensão volta a
        incrementar — o turno já está encerrado."""
        budget = TurnBudget(config=LoopCapConfig(max_tool_calls_per_turn=0))
        assert budget.record_tool_call(None) == "max_tool_calls_per_turn"
        assert budget.record_subagent_spawn() == "max_tool_calls_per_turn"
        assert budget.record_aitl_call() == "max_tool_calls_per_turn"
        assert budget.subagent_spawns == 0
        assert budget.aitl_calls == 0
