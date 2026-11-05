"""Fábrica de mocks de `lancedb.AsyncTable`/`AsyncConnection` fiéis à API real.

Existe porque `MagicMock()`/`AsyncMock()` genéricos aceitam qualquer método
ou kwarg, certo ou errado — foi assim que pelo menos três bugs reais (nome
de método inexistente, kwargs que a API não aceita mais, e `await` faltando
antes de encadear um método async) sobreviveram sem serem notados: cada
mock genérico aceitava a chamada errada silenciosamente. `create_autospec`
contra a classe real instalada valida assinatura de verdade — uma chamada
com método/kwarg inexistente levanta `AttributeError`/`TypeError` no teste,
não passa despercebida.
"""

from __future__ import annotations

from unittest.mock import create_autospec

import lancedb


def fake_async_table() -> lancedb.AsyncTable:
    """Mock de `AsyncTable` com assinaturas reais validadas em toda chamada."""
    return create_autospec(lancedb.AsyncTable, instance=True)


def fake_async_connection() -> lancedb.AsyncConnection:
    """Mock de `AsyncConnection` com assinaturas reais validadas em toda chamada."""
    return create_autospec(lancedb.AsyncConnection, instance=True)
