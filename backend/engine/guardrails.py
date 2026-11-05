"""``LoopCapConfig``/``TurnBudget`` — teto de volume de chamadas por turno.

Complementa (não substitui) a detecção de repetição já embutida em
``conversation_loop.py`` (mesma tool call idêntica N vezes seguidas): aqui
o eixo é volume total do turno, não repetição — quantas tool calls, spawns
de subagente e chamadas AITL um único turno pode fazer antes do loop travar
de propósito. Sem isso, um turno com centenas de tool calls legítimas mas
nunca repetidas (caro, não "preso") nunca disparava guard nenhum.

``TurnBudget`` é instanciado uma vez por chamada a ``run_conversation``
(um turno) e passado adiante pra ``execute_tool_batch``/``run_subagent`` —
o mesmo objeto, não um novo por iteração do loop, porque o teto é por
turno inteiro, não por volta.

Nota de escopo: o timeout+cap do AITL (``backend/tools/aitl.py::
ask_parent_agent``) continua fora daqui. O tool em si já foi migrado pro
registry nativo (``@vtool``, ``FallbackChatClient``, ``ToolContext``) e é
invocado como ``ToolSpec`` nativo pelo motor, sem
nenhum ``TurnBudget`` em escopo (esse objeto só existe dentro do loop de
conversa nativo, `run_conversation`/`execute_tool_batch`). O tool está
pronto pra receber o teto assim que for chamado de dentro do motor
nativo de verdade — ``TurnBudget.record_aitl_call`` já existe pronto
pra esse dia, sem consumidor real ainda.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from backend.tools.registry import ToolSpec

#: Categorias de tool (``ToolExtras.category``) tratadas como "chamada de
#: rede" pro teto dedicado — cobre as tools que de fato saem pra internet.
NETWORK_CATEGORIES = frozenset({"web", "browser", "mcp", "github"})


@dataclass(slots=True)
class LoopCapConfig:
    """Tetos por turno. ``None`` desliga o teto daquela dimensão."""

    max_tool_calls_per_turn: int | None = 100
    max_subagent_spawns_per_turn: int | None = 10
    max_network_calls_per_turn: int | None = 40
    max_aitl_calls_per_turn: int | None = 10


@dataclass(slots=True)
class TurnBudget:
    """Contador mutável de um turno — um objeto por chamada a
    ``run_conversation``, compartilhado entre o loop principal e qualquer
    subagente que ele dispare (mesmo teto vale pro turno inteiro, não por
    instância do motor)."""

    config: LoopCapConfig = field(default_factory=LoopCapConfig)
    tool_calls: int = 0
    subagent_spawns: int = 0
    network_calls: int = 0
    aitl_calls: int = 0
    exceeded: str | None = None
    """Nome do campo de ``LoopCapConfig`` que estourou primeiro — uma vez
    setado, fica travado (latched): chamadas seguintes de qualquer
    ``record_*`` devolvem o mesmo código sem reavaliar, e nenhum contador
    incrementa mais. Isso garante uma única emissão de
    ``ErrorSignal(code="LOOP_CAP_EXCEEDED")`` por turno."""

    def record_tool_call(self, spec: ToolSpec | None) -> str | None:
        """Registra uma tool call. Devolve o campo estourado (bloqueando a
        execução dessa chamada) ou ``None`` se dentro do teto."""
        if self.exceeded is not None:
            return self.exceeded
        cap = self.config.max_tool_calls_per_turn
        if cap is not None and self.tool_calls >= cap:
            self.exceeded = "max_tool_calls_per_turn"
            return self.exceeded
        if spec is not None and spec.extras.category in NETWORK_CATEGORIES:
            net_cap = self.config.max_network_calls_per_turn
            if net_cap is not None and self.network_calls >= net_cap:
                self.exceeded = "max_network_calls_per_turn"
                return self.exceeded
            self.network_calls += 1
        self.tool_calls += 1
        return None

    def record_subagent_spawn(self) -> str | None:
        """Registra o disparo de um subagente. Devolve o campo estourado
        (bloqueando o spawn) ou ``None`` se dentro do teto."""
        if self.exceeded is not None:
            return self.exceeded
        cap = self.config.max_subagent_spawns_per_turn
        if cap is not None and self.subagent_spawns >= cap:
            self.exceeded = "max_subagent_spawns_per_turn"
            return self.exceeded
        self.subagent_spawns += 1
        return None

    def record_aitl_call(self) -> str | None:
        """Registra uma chamada AITL. Devolve o campo estourado (bloqueando
        a chamada) ou ``None`` se dentro do teto."""
        if self.exceeded is not None:
            return self.exceeded
        cap = self.config.max_aitl_calls_per_turn
        if cap is not None and self.aitl_calls >= cap:
            self.exceeded = "max_aitl_calls_per_turn"
            return self.exceeded
        self.aitl_calls += 1
        return None
