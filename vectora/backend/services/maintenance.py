"""Barreira compartilhada para operações que substituem arquivos de estado."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar

_condition = asyncio.Condition()
_maintenance_active = False
_active_operations = 0
_maintenance_bypass: ContextVar[bool] = ContextVar("maintenance_bypass", default=False)


async def wait_until_available() -> None:
    """Aguarda o fim de uma janela de manutenção antes de abrir estado."""
    if _maintenance_bypass.get():
        return
    async with _condition:
        await _condition.wait_for(lambda: not _maintenance_active)


@asynccontextmanager
async def storage_operation() -> AsyncIterator[None]:
    """Registra uma operação que mantém arquivos restauráveis em uso."""
    global _active_operations
    async with _condition:
        if not _maintenance_bypass.get():
            await _condition.wait_for(lambda: not _maintenance_active)
        _active_operations += 1
    try:
        yield
    finally:
        async with _condition:
            _active_operations -= 1
            _condition.notify_all()


@asynccontextmanager
async def maintenance_bypass() -> AsyncIterator[None]:
    """Permite reabrir consumidores enquanto a janela controla o processo.

    Deve ser usado apenas pelo fluxo de restore, depois que os consumidores
    antigos foram fechados e antes de a janela ser liberada.
    """
    token = _maintenance_bypass.set(True)
    try:
        yield
    finally:
        _maintenance_bypass.reset(token)


@asynccontextmanager
async def maintenance_window() -> AsyncIterator[None]:
    """Bloqueia novas aberturas de consumidores durante uma promoção."""
    global _maintenance_active
    async with _condition:
        _maintenance_active = True
        await _condition.wait_for(lambda: _active_operations == 0)
    try:
        yield
    finally:
        async with _condition:
            _maintenance_active = False
            _condition.notify_all()
