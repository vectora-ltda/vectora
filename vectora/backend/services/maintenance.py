"""Barreira compartilhada para operações que substituem arquivos de estado."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

_condition = asyncio.Condition()
_maintenance_active = False


async def wait_until_available() -> None:
    """Aguarda o fim de uma janela de manutenção antes de abrir estado."""
    async with _condition:
        await _condition.wait_for(lambda: not _maintenance_active)


@asynccontextmanager
async def maintenance_window() -> AsyncIterator[None]:
    """Bloqueia novas aberturas de consumidores durante uma promoção."""
    global _maintenance_active
    async with _condition:
        _maintenance_active = True
    try:
        yield
    finally:
        async with _condition:
            _maintenance_active = False
            _condition.notify_all()
