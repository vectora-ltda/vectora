"""IDs "branded" (`typing.NewType`) para identificadores do motor nativo
que hoje são todos `str` puro e fáceis de trocar por engano num call site
com múltiplos parâmetros de string.

Zero custo em runtime: um valor `CorrelationId` É o `str` subjacente, sem
wrapper/alocação — o `NewType` existe só pro type checker (`ty`) recusar
passar um `CapabilityToken` onde se espera um `CorrelationId` (e vice
versa), sem exigir conversão explícita de volta pra `str` em nenhum ponto
de uso normal (fica compatível com toda API que já espera `str`).
"""

from __future__ import annotations

from typing import NewType

CorrelationId = NewType("CorrelationId", str)
"""Identificador de intenção de delegação a subagente
(`SubagentSpec.correlation_id`) — dedup e alvo de cancelamento explícito."""

CapabilityToken = NewType("CapabilityToken", str)
"""HMAC(secret, correlation_id) — prova de posse pra autorizar
`request_hard_interrupt`. Nunca deve ser confundido com o próprio
`CorrelationId` que ele assina (mesmo formato de string, semântica
completamente diferente — um é público/previsível, o outro é a prova de
autorização)."""
