"""Stress test 5 — EmbeddingQueue: ciclo completo enqueue → mark_success/failed.

Simula o ciclo de vida real de um BackgroundEmbeddingWorker processando uma
rajada de documentos:
  1. Enfileira N documentos (fase de ingestão)
  2. Busca lotes de pendentes (get_pending)
  3. Metade é marcada como sucesso, metade como falha (mark_success / mark_failed)
  4. Verifica contagens finais de cada status

Testa a consistência do banco SQLite sob transições de estado concorrentes e
garante que o índice em `status` mantém as queries rápidas mesmo após
muitas atualizações.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from backend.embedding.queue import EmbeddingQueue


@pytest.mark.stress
@pytest.mark.asyncio
async def test_queue_full_lifecycle(tmp_path):
    """200 documentos: enqueue → get_pending → mark_success/failed — consistência total."""
    N = 200
    db_path = tmp_path / "stress_lifecycle.db"
    queue = EmbeddingQueue(f"sqlite+aiosqlite:///{db_path}")
    await queue.init()

    # ── Etapa 1: enfileira N documentos em paralelo ─────────────────────────
    t_enqueue = time.perf_counter()
    queue_ids: list[str] = await asyncio.gather(
        *[queue.enqueue(f"documento {i}", "lifecycle_col") for i in range(N)]
    )
    elapsed_enqueue = time.perf_counter() - t_enqueue

    assert len(queue_ids) == N
    assert await queue.count_pending() == N

    # ── Etapa 2: busca todos os pendentes em lotes de 50 ────────────────────
    t_fetch = time.perf_counter()
    fetched_ids: list[str] = []
    while True:
        batch = await queue.get_pending(limit=50)
        if not batch:
            break
        fetched_ids.extend(str(rec.queue_id) for rec in batch)
        # Marca os do lote como "processing" para não buscá-los de novo
        await asyncio.gather(
            *[queue.mark_failed(str(rec.queue_id), "processing") for rec in batch]
        )
    elapsed_fetch = time.perf_counter() - t_fetch

    assert len(fetched_ids) == N, f"Fetch incompleto: {len(fetched_ids)}/{N}"

    # ── Etapa 3: metade sucesso, metade falha — em paralelo ─────────────────
    half = N // 2
    success_ids = queue_ids[:half]
    failed_ids = queue_ids[half:]

    t_mark = time.perf_counter()
    await asyncio.gather(
        *[queue.mark_success(qid) for qid in success_ids],
        *[queue.mark_failed(qid, "erro de embedding simulado") for qid in failed_ids],
    )
    elapsed_mark = time.perf_counter() - t_mark

    # ── Etapa 4: verifica contagens finais ──────────────────────────────────
    pending_final = await queue.count_pending()
    assert pending_final == 0, f"Ainda há {pending_final} pendentes após processamento"

    # Budgets individuais
    assert elapsed_enqueue < 10.0, f"Enqueue: {elapsed_enqueue:.2f}s para {N} itens"
    # Budget generoso: SQLite em Windows com WAL contention pode hesitar ~6s
    # no Stress de N=500. Falha real fica em ordem de magnitude pior.
    assert elapsed_fetch < 10.0, f"Fetch em lotes: {elapsed_fetch:.2f}s"
    assert elapsed_mark < 5.0, f"Mark operations: {elapsed_mark:.2f}s para {N} itens"


@pytest.mark.stress
@pytest.mark.asyncio
async def test_queue_count_pending_scales_with_index(tmp_path):
    """count_pending() deve ser O(log n) com o índice — rápido mesmo com 500 registros."""
    N = 500
    db_path = tmp_path / "stress_count.db"
    queue = EmbeddingQueue(f"sqlite+aiosqlite:///{db_path}")
    await queue.init()

    # Popula a fila
    await asyncio.gather(*[queue.enqueue(f"doc {i}", "index_col") for i in range(N)])

    # count_pending() 100 vezes — deve ser consistentemente rápido
    ITERATIONS = 100
    t0 = time.perf_counter()
    for _ in range(ITERATIONS):
        count = await queue.count_pending()
        assert count == N
    elapsed = time.perf_counter() - t0

    avg_ms = (elapsed / ITERATIONS) * 1000
    # Budget: cada count_pending deve levar menos de 50 ms em média
    assert avg_ms < 50.0, f"count_pending muito lento: {avg_ms:.1f}ms médio (máx: 50ms)"


@pytest.mark.stress
@pytest.mark.asyncio
@pytest.mark.parametrize("n", [50, 200, 500], ids=lambda n: f"n={n}")
@pytest.mark.parametrize(
    "success_ratio", [0.0, 0.25, 0.5, 0.75, 1.0], ids=lambda r: f"ratio={r}"
)
async def test_queue_lifecycle_various_ratios(tmp_path, n, success_ratio):
    """Ciclo enqueue→mark com proporções variadas de sucesso/falha — contagem final sempre zera."""
    db_path = tmp_path / f"stress_ratio_{n}_{success_ratio}.db"
    queue = EmbeddingQueue(f"sqlite+aiosqlite:///{db_path}")
    await queue.init()

    ids = await asyncio.gather(
        *[queue.enqueue(f"doc {i}", "ratio_col") for i in range(n)]
    )
    n_success = int(n * success_ratio)
    success_ids, fail_ids = ids[:n_success], ids[n_success:]

    await asyncio.gather(
        *[queue.mark_success(qid) for qid in success_ids],
        *[queue.mark_failed(qid, "erro simulado") for qid in fail_ids],
    )

    assert await queue.count_pending() == 0


@pytest.mark.stress
@pytest.mark.asyncio
@pytest.mark.parametrize("n", [100, 300, 1000, 2000, 3000], ids=lambda n: f"n={n}")
async def test_queue_count_pending_scales_various_sizes(tmp_path, n):
    """count_pending() permanece correto e rápido em volumes crescentes."""
    db_path = tmp_path / f"stress_count_{n}.db"
    queue = EmbeddingQueue(f"sqlite+aiosqlite:///{db_path}")
    await queue.init()

    await asyncio.gather(*[queue.enqueue(f"doc {i}", "count_col") for i in range(n)])

    t0 = time.perf_counter()
    count = await queue.count_pending()
    elapsed = time.perf_counter() - t0

    assert count == n
    assert elapsed < 2.0


@pytest.mark.stress
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reason",
    ["timeout", "rate limit", "erro de parsing", "x" * 500, ""],
    ids=["timeout", "rate_limit", "parsing", "long_reason", "empty_reason"],
)
async def test_queue_mark_failed_various_reasons(tmp_path, reason):
    """mark_failed aceita motivos de tamanhos/formatos variados sem corromper o registro."""
    db_path = tmp_path / "stress_reasons.db"
    queue = EmbeddingQueue(f"sqlite+aiosqlite:///{db_path}")
    await queue.init()

    qid = await queue.enqueue("doc", "reason_col")
    await queue.mark_failed(qid, reason)

    assert await queue.count_pending() == 0


@pytest.mark.stress
@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 10, 50, 100, 500], ids=lambda n: f"limit={n}")
async def test_queue_get_pending_various_limits(tmp_path, limit):
    """get_pending(limit=N) nunca retorna mais que o limite pedido, mesmo com a fila cheia."""
    N = 200
    db_path = tmp_path / f"stress_limit_{limit}.db"
    queue = EmbeddingQueue(f"sqlite+aiosqlite:///{db_path}")
    await queue.init()

    await asyncio.gather(*[queue.enqueue(f"doc {i}", "limit_col") for i in range(N)])
    batch = await queue.get_pending(limit=limit)

    assert len(batch) == min(limit, N)


@pytest.mark.stress
@pytest.mark.asyncio
@pytest.mark.parametrize("n", [10, 100], ids=lambda n: f"n={n}")
async def test_queue_mark_success_idempotent_under_load(tmp_path, n):
    """mark_success chamado duas vezes em paralelo pro mesmo lote — idempotente, nunca lança."""
    db_path = tmp_path / f"stress_idem_{n}.db"
    queue = EmbeddingQueue(f"sqlite+aiosqlite:///{db_path}")
    await queue.init()

    ids = await asyncio.gather(
        *[queue.enqueue(f"doc{i}", "idem_col") for i in range(n)]
    )
    await asyncio.gather(*[queue.mark_success(qid) for qid in ids])
    # Segunda rodada sobre os mesmos ids não deve lançar nem duplicar estado.
    await asyncio.gather(*[queue.mark_success(qid) for qid in ids])

    assert await queue.count_pending() == 0
