"""``ToolContext`` — contexto de execução injetado em toda tool nativa.
Substitui ``Annotated[RunnableConfig, InjectedToolArg]`` — nunca vai pro
JSON Schema exposto ao LLM (``vtool``, ``backend/tools/registry.py``,
filtra o parâmetro `ctx` na geração do schema).

``VectoraContext`` (``backend/vtypes/context.py``) já era um dataclass
próprio, populado pelo motor nativo a cada turno. Reusar em vez de
duplicar: mesmos campos, mesmo ``ctx_from_config`` de compatibilidade,
agora também a interface direta que toda tool nativa recebe por injeção
do loop de conversa
(``backend/engine/conversation_loop.py``).
"""

from __future__ import annotations

from backend.vtypes.context import VectoraContext as ToolContext
from backend.vtypes.context import ctx_from_config

__all__ = ["ToolContext", "ctx_from_config"]
