"""Testes para backend/storage/lancedb/optimize.py.

Cobertura era zero antes destes testes — foi assim que o bug real
(`table.cleanup_old_versions()` chamando um método que não existe mais em
`AsyncTable`, engolido pelo `try/except` como warning genérico) passou
despercebido. `optimize_table` agora usa `table.optimize(cleanup_older_than=...)`,
a API unificada de compactação + limpeza de versões antigas.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta

from backend.storage.lancedb.optimize import optimize_table, schedule_optimize


@dataclass
class _FakeCompactionStats:
    fragments_removed: int = 3
    fragments_added: int = 1


@dataclass
class _FakePruneStats:
    old_versions_removed: int = 2
    bytes_removed: int = 4096


@dataclass
class _FakeOptimizeStats:
    compaction: _FakeCompactionStats
    prune: _FakePruneStats


class _FakeTable:
    def __init__(self, name: str = "articles") -> None:
        self.name = name
        self.optimize_calls: list[dict] = []
        self._should_fail = False

    async def optimize(self, *, cleanup_older_than=None, **_kwargs):
        self.optimize_calls.append({"cleanup_older_than": cleanup_older_than})
        if self._should_fail:
            msg = "storage backend indisponível"
            raise RuntimeError(msg)
        return _FakeOptimizeStats(_FakeCompactionStats(), _FakePruneStats())


class TestOptimizeTable:
    async def test_chama_optimize_unificado_e_retorna_true(self):
        table = _FakeTable()

        ok = await optimize_table(table, cleanup_older_than_s=3600)

        assert ok is True
        assert len(table.optimize_calls) == 1
        assert table.optimize_calls[0]["cleanup_older_than"] == timedelta(seconds=3600)
        # Não existe mais uma chamada separada a cleanup_old_versions — a
        # API antiga não tem esse método em AsyncTable (regressão real que
        # este teste protege).
        assert not hasattr(table, "cleanup_old_versions")

    async def test_falha_no_optimize_retorna_false_sem_propagar(self):
        table = _FakeTable()
        table._should_fail = True

        ok = await optimize_table(table)

        assert ok is False


class TestScheduleOptimize:
    async def test_loop_roda_optimize_apos_o_intervalo(self):
        table = _FakeTable()

        task = schedule_optimize(table, interval_s=0.01, cleanup_older_than_s=60)
        await asyncio.sleep(0.05)
        task.cancel()
        # O loop captura CancelledError e retorna normalmente (shutdown
        # silencioso, por desenho) — await não propaga a exceção.
        await task

        assert len(table.optimize_calls) >= 1

    async def test_erro_inesperado_no_loop_nao_encerra_a_task(self):
        table = _FakeTable()
        table._should_fail = True

        task = schedule_optimize(table, interval_s=0.01)
        await asyncio.sleep(0.03)

        # A task continua viva mesmo com optimize() falhando repetidamente —
        # falha esperada aqui é return False (capturada dentro de
        # optimize_table), não uma exceção vazando pro loop.
        assert not task.done()
        task.cancel()
        await task
