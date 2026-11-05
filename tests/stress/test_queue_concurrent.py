"""Stress test 1 — EmbeddingQueue: enqueue concorrente.

Dispara N coroutines simultâneas de enqueue() contra uma única fila SQLite
e verifica que:
  - todos os queue_ids são únicos (sem colisão de UUID)
  - nenhuma escrita é perdida (count_pending == N)
  - o tempo total permanece dentro do budget aceitável

Testa: WAL mode, busy_timeout, Index(status) e ausência de deadlock sob
carga de escrita paralela — o mesmo padrão do BackgroundEmbeddingWorker +
ingest_docs rodando ao mesmo tempo em produção.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from backend.embedding.queue import EmbeddingQueue


@pytest.mark.stress
@pytest.mark.asyncio
async def test_queue_concurrent_enqueue(tmp_path):
    """100 enqueues simultâneos — sem perda, sem colisão, dentro do budget."""
    N = 100
    db_path = tmp_path / "stress_queue.db"
    queue = EmbeddingQueue(f"sqlite+aiosqlite:///{db_path}")
    await queue.init()

    texts = [f"documento de stress número {i}" for i in range(N)]

    t0 = time.perf_counter()
    queue_ids = await asyncio.gather(
        *[queue.enqueue(text, "stress_collection") for text in texts]
    )
    elapsed = time.perf_counter() - t0

    # Todos os IDs devem ser únicos
    assert len(set(queue_ids)) == N, "Colisão de queue_id detectada"

    # Nenhuma escrita pode ter sido perdida
    pending = await queue.count_pending()
    assert pending == N, f"Esperado {N} pendentes, encontrado {pending}"

    # Budget: 100 escritas SQLite locais devem completar em menos de 10 s
    assert elapsed < 10.0, f"Tempo excessivo: {elapsed:.2f}s para {N} enqueues"


@pytest.mark.stress
@pytest.mark.asyncio
@pytest.mark.parametrize("n", [1, 10, 50, 250, 500, 1000], ids=lambda n: f"n={n}")
async def test_queue_concurrent_enqueue_scales(tmp_path, n):
    """Enqueue concorrente em volumes crescentes — sem perda, sem colisão em nenhuma escala."""
    db_path = tmp_path / f"stress_scale_{n}.db"
    queue = EmbeddingQueue(f"sqlite+aiosqlite:///{db_path}")
    await queue.init()

    queue_ids = await asyncio.gather(
        *[queue.enqueue(f"documento {i}", "scale_col") for i in range(n)]
    )

    assert len(set(queue_ids)) == n
    assert await queue.count_pending() == n


@pytest.mark.stress
@pytest.mark.asyncio
@pytest.mark.parametrize("text_len", [10, 100, 1_000, 10_000], ids=lambda n: f"len={n}")
async def test_queue_concurrent_enqueue_various_text_lengths(tmp_path, text_len):
    """Enqueue concorrente com payloads de tamanhos variados — texto grande não corrompe a fila."""
    N = 50
    db_path = tmp_path / f"stress_len_{text_len}.db"
    queue = EmbeddingQueue(f"sqlite+aiosqlite:///{db_path}")
    await queue.init()

    text = "x" * text_len
    queue_ids = await asyncio.gather(
        *[queue.enqueue(text, "len_col") for _ in range(N)]
    )

    assert len(set(queue_ids)) == N
    assert await queue.count_pending() == N


@pytest.mark.stress
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "n_collections", [1, 5, 20, 50], ids=lambda n: f"collections={n}"
)
async def test_queue_concurrent_enqueue_across_collections(tmp_path, n_collections):
    """Enqueue distribuído por múltiplas coleções ao mesmo tempo — isolamento por collection."""
    per_collection = 20
    db_path = tmp_path / f"stress_multicol_{n_collections}.db"
    queue = EmbeddingQueue(f"sqlite+aiosqlite:///{db_path}")
    await queue.init()

    tasks = [
        queue.enqueue(f"doc {c}-{i}", f"col_{c}")
        for c in range(n_collections)
        for i in range(per_collection)
    ]
    queue_ids = await asyncio.gather(*tasks)

    total = n_collections * per_collection
    assert len(set(queue_ids)) == total
    assert await queue.count_pending() == total


@pytest.mark.stress
@pytest.mark.asyncio
async def test_queue_concurrent_burst_pattern(tmp_path):
    """3 rajadas sucessivas de 100 enqueues — sem degradação de uma rajada pra outra."""
    db_path = tmp_path / "stress_burst.db"
    queue = EmbeddingQueue(f"sqlite+aiosqlite:///{db_path}")
    await queue.init()

    total = 0
    for wave in range(3):
        ids = await asyncio.gather(
            *[queue.enqueue(f"burst {wave}-{i}", "burst_col") for i in range(100)]
        )
        assert len(set(ids)) == 100
        total += 100

    assert await queue.count_pending() == total


@pytest.mark.stress
@pytest.mark.asyncio
async def test_queue_concurrent_no_id_collision_high_volume(tmp_path):
    """2000 enqueues simultâneos — UUID4 não colide mesmo em alto volume."""
    N = 2000
    db_path = tmp_path / "stress_highvol.db"
    queue = EmbeddingQueue(f"sqlite+aiosqlite:///{db_path}")
    await queue.init()

    ids = await asyncio.gather(
        *[queue.enqueue(f"doc {i}", "highvol_col") for i in range(N)]
    )

    assert len(set(ids)) == N


@pytest.mark.stress
@pytest.mark.asyncio
@pytest.mark.parametrize("n", [5, 15, 30], ids=lambda n: f"n={n}")
async def test_queue_concurrent_enqueue_same_text_repeated(tmp_path, n):
    """N enqueues do MESMO texto — cada chamada gera um queue_id distinto (não é dedup)."""
    db_path = tmp_path / f"stress_dup_{n}.db"
    queue = EmbeddingQueue(f"sqlite+aiosqlite:///{db_path}")
    await queue.init()

    ids = await asyncio.gather(
        *[queue.enqueue("texto idêntico repetido", "dup_col") for _ in range(n)]
    )

    assert len(set(ids)) == n
    assert await queue.count_pending() == n
