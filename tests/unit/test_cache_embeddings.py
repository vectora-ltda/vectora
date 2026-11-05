"""Cache de embeddings (``services/cache_embeddings.py``)."""

from __future__ import annotations

import pytest

from backend.embedding import cache_embeddings
from backend.persistence.kv import reset_kv


@pytest.fixture(autouse=True)
def _reset(_no_nats_sidecar):
    reset_kv()
    yield
    reset_kv()


async def test_inativo_sempre_recalcula(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_embeddings, "_redis_active", lambda: False)
    calls = {"n": 0}

    def embed(text: str) -> list[float]:
        calls["n"] += 1
        return [1.0, 2.0]

    assert await cache_embeddings.embed_query_cached("oi", "m", embed) == [1.0, 2.0]
    await cache_embeddings.embed_query_cached("oi", "m", embed)
    assert calls["n"] == 2  # sem cache: chama embed nas duas


async def test_ativo_cacheia(monkeypatch: pytest.MonkeyPatch) -> None:
    # get_kv() devolve MemoryKV (sem redis_url nos testes) — serve de backend.
    monkeypatch.setattr(cache_embeddings, "_redis_active", lambda: True)
    calls = {"n": 0}

    def embed(text: str) -> list[float]:
        calls["n"] += 1
        return [0.1, 0.2, 0.3]

    first = await cache_embeddings.embed_query_cached("pergunta", "m", embed)
    second = await cache_embeddings.embed_query_cached("pergunta", "m", embed)
    assert first == second == [0.1, 0.2, 0.3]
    assert calls["n"] == 1  # segundo é hit de cache


async def test_textos_distintos_nao_colidem(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cache_embeddings, "_redis_active", lambda: True)

    def embed(text: str) -> list[float]:
        return [float(len(text))]

    a = await cache_embeddings.embed_query_cached("abc", "m", embed)
    b = await cache_embeddings.embed_query_cached("abcd", "m", embed)
    assert a == [3.0]
    assert b == [4.0]


async def test_embedding_assincrono_nao_invoca_asyncio_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cache_embeddings, "_redis_active", lambda: False)
    calls = {"n": 0}

    async def embed(text: str) -> list[float]:
        calls["n"] += 1
        return [float(len(text))]

    result = await cache_embeddings.embed_query_cached("consulta", "m", embed)

    assert result == [8.0]
    assert calls["n"] == 1
