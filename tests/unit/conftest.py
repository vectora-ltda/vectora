"""Fixtures mínimas compartilhadas — KISS."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def _no_nats_sidecar(monkeypatch: pytest.MonkeyPatch):
    """Neutraliza o sidecar NATS: ``get_kv``/``get_mq`` caem no fallback de memória.

    Sem isso, todo teste que assume ``MemoryKV``/``MemoryMQ`` passa a subir o
    ``nats-server`` real assim que o binário existe em ``resources/`` (após
    ``scons nats``) — deixando a suíte dependente da presença do binário e
    compartilhando o store JetStream em ``~/.vectora/nats`` entre os testes.

    Testes que querem o ``NatsKV``/``NatsMQ`` de verdade ou re-mockam
    ``ensure_nats_sidecar`` no próprio corpo (o último ``setattr`` vence) ou
    vivem em ``test_nats_sidecar.py`` / ``test_nats_mq_kv_real.py`` (que não
    dependem deste fixture).
    """
    from backend.scheduling import nats_sidecar

    monkeypatch.setattr(
        nats_sidecar, "ensure_nats_sidecar", AsyncMock(return_value=None)
    )


@pytest.fixture
def _no_thread_persistence(monkeypatch: pytest.MonkeyPatch):
    """Neutraliza ``threads._increment_message_count``.

    ``stream_engine_events()`` (native_stream.py) dispara essa chamada
    fire-and-forget no 1º ``TokenEvent`` de qualquer stream (ver
    ``_mark_thread_has_content``) —
    sem mock, abre uma conexão ``aiosqlite`` REAL a ``~/.vectora/checkpoints.db``.
    Testes que só exercitam semântica de streaming (não bookkeeping de sessão)
    vazavam essa conexão entre si — a task nunca é esperada, então o singleton
    ``_db_conn`` do módulo ``threads`` ficava preso a um event loop de um teste
    já encerrado, travando ``TestClient.__enter__`` de testes de lifespan bem
    mais adiante na suíte.
    """
    from backend.api.handlers import threads

    monkeypatch.setattr(
        threads, "_increment_message_count", AsyncMock(return_value=None)
    )
