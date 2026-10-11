"""Testes dos contratos de persistência e filas."""

from __future__ import annotations

from typing import get_type_hints

from backend.vtypes.storage import HealthResult, QueueTask


def test_storage_contracts_keep_health_and_queue_shapes_explicit() -> None:
    """Todos os backends compartilham o mesmo resultado de saúde."""
    assert set(get_type_hints(HealthResult)) == {"ok", "error"}
    assert {"id", "kind", "payload"} <= set(get_type_hints(QueueTask))
