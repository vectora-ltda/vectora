"""Stress test 2 — EmbeddingQueue: contenção leitor/escritor.

Simula o padrão de produção onde ingest_docs (escritor) e
BackgroundEmbeddingWorker (leitor) operam na mesma fila SQLite ao mesmo tempo.

  - N writers fazem enqueue() em paralelo
  - N readers fazem get_pending() em paralelo
  - tudo simultâneo (asyncio.gather de ambos os grupos)

Verifica:
  - readers nunca levantam exceção (sem "database is locked")
  - writers persistem todos os registros (count_pending == N_WRITES)
  - o tempo total está dentro do budget
"""

from __future__ import annotations

import asyncio
import time

import pytest

from backend.embedding.queue import EmbeddingQueue


@pytest.mark.stress
@pytest.mark.asyncio
async def test_queue_reader_writer_contention(tmp_path):
    """50 writers + 10 readers simultâneos — sem deadlock nem perda de dados."""
    N_WRITES = 50
    N_READS = 10

    db_path = tmp_path / "stress_rw.db"
    queue = EmbeddingQueue(f"sqlite+aiosqlite:///{db_path}")
    await queue.init()

    async def writer(i: int) -> str:
        return await queue.enqueue(f"texto do writer {i}", "rw_collection")

    async def reader() -> list:
        return await queue.get_pending(limit=5)

    t0 = time.perf_counter()
    results = await asyncio.gather(
        *[writer(i) for i in range(N_WRITES)],
        *[reader() for _ in range(N_READS)],
        return_exceptions=True,
    )
    elapsed = time.perf_counter() - t0

    # Nenhuma operação pode ter levantado exceção
    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors, f"Exceções durante contenção R/W: {errors}"

    # Todos os registros foram persistidos
    pending = await queue.count_pending()
    assert pending == N_WRITES, f"Esperado {N_WRITES} pendentes, encontrado {pending}"

    # Budget: 50 writes + 10 reads em menos de 10 s
    assert elapsed < 10.0, f"Tempo excessivo: {elapsed:.2f}s"


@pytest.mark.stress
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("writers", "readers"),
    [(10, 2), (50, 10), (100, 20), (200, 5), (20, 50)],
    ids=["w10_r2", "w50_r10", "w100_r20", "w200_r5", "w20_r50"],
)
async def test_queue_reader_writer_various_ratios(tmp_path, writers, readers):
    """Proporções variadas de leitores/escritores concorrentes — nunca há exceção nem perda."""
    db_path = tmp_path / f"stress_rw_{writers}_{readers}.db"
    queue = EmbeddingQueue(f"sqlite+aiosqlite:///{db_path}")
    await queue.init()

    async def writer(i: int) -> str:
        return await queue.enqueue(f"w{i}", "rw_ratio_col")

    async def reader() -> list:
        return await queue.get_pending(limit=5)

    results = await asyncio.gather(
        *[writer(i) for i in range(writers)],
        *[reader() for _ in range(readers)],
        return_exceptions=True,
    )

    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors, f"Exceções durante contenção R/W: {errors}"
    assert await queue.count_pending() == writers


@pytest.mark.stress
@pytest.mark.asyncio
@pytest.mark.parametrize("waves", [1, 3, 5, 10], ids=lambda n: f"waves={n}")
async def test_queue_reader_writer_sequential_waves(tmp_path, waves):
    """Múltiplas rajadas sucessivas de leitura/escrita concorrente — sem degradação entre ondas."""
    db_path = tmp_path / f"stress_waves_{waves}.db"
    queue = EmbeddingQueue(f"sqlite+aiosqlite:///{db_path}")
    await queue.init()
    per_wave = 20

    async def reader() -> list:
        return await queue.get_pending(limit=5)

    for wave in range(waves):

        async def writer(i: int, wave: int = wave) -> str:
            return await queue.enqueue(f"wave{wave}-{i}", "wave_col")

        results = await asyncio.gather(
            *[writer(i) for i in range(per_wave)],
            *[reader() for _ in range(5)],
            return_exceptions=True,
        )
        errors = [r for r in results if isinstance(r, Exception)]
        assert not errors, f"Exceções na onda {wave}: {errors}"

    assert await queue.count_pending() == waves * per_wave


@pytest.mark.stress
@pytest.mark.asyncio
@pytest.mark.parametrize("n", [50, 100, 300], ids=lambda n: f"n={n}")
async def test_queue_mixed_read_write_mark_concurrency(tmp_path, n):
    """Leitura, escrita e mark_success acontecendo ao mesmo tempo — consistência final garantida."""
    db_path = tmp_path / f"stress_mixed_{n}.db"
    queue = EmbeddingQueue(f"sqlite+aiosqlite:///{db_path}")
    await queue.init()

    half = n // 2
    seed_ids = await asyncio.gather(
        *[queue.enqueue(f"seed{i}", "mixed_col") for i in range(half)]
    )

    async def writer(i: int) -> str:
        return await queue.enqueue(f"new{i}", "mixed_col")

    async def reader() -> list:
        return await queue.get_pending(limit=10)

    async def marker(qid: str) -> None:
        await queue.mark_success(qid)

    results = await asyncio.gather(
        *[writer(i) for i in range(half)],
        *[reader() for _ in range(5)],
        *[marker(qid) for qid in seed_ids],
        return_exceptions=True,
    )

    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors, f"Exceções na concorrência mista: {errors}"
    assert await queue.count_pending() == half


@pytest.mark.stress
@pytest.mark.asyncio
@pytest.mark.parametrize("n_readers", [1, 5, 10, 25, 50], ids=lambda n: f"readers={n}")
async def test_queue_reader_only_concurrency(tmp_path, n_readers):
    """N leitores concorrentes contra fila pré-populada — nenhuma exceção, leituras consistentes."""
    db_path = tmp_path / f"stress_readers_{n_readers}.db"
    queue = EmbeddingQueue(f"sqlite+aiosqlite:///{db_path}")
    await queue.init()

    await asyncio.gather(*[queue.enqueue(f"seed{i}", "readers_col") for i in range(30)])

    async def reader() -> list:
        return await queue.get_pending(limit=10)

    results = await asyncio.gather(
        *[reader() for _ in range(n_readers)], return_exceptions=True
    )

    errors = [r for r in results if isinstance(r, Exception)]
    assert not errors, f"Exceções em leitura concorrente pura: {errors}"
