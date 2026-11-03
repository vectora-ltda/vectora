"""Contratos comuns dos backends de persistência e filas."""

from __future__ import annotations

from typing import NotRequired, TypedDict


class HealthResult(TypedDict):
    """Resultado uniforme de health checks de qualquer backend."""

    ok: bool
    error: str | None


class QueueTask(TypedDict, total=False):
    """Envelope mínimo de uma tarefa de fila persistida."""

    id: str
    kind: str
    payload: dict[str, object]
    attempts: int
    available_at: str
    error: NotRequired[str | None]
