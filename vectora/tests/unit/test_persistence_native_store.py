"""Exercício real de ``VectoraStore`` — implementação direta de
``aget``/``aput``/``adelete``/``asearch`` sobre ``aiosqlite`` (o único
teste anterior, ``test_e_b_parity.py::TestBuildStore``, fazia só
``isinstance(store, VectoraStore)``, sem nunca chamar os métodos de
fato)."""

from __future__ import annotations

import pytest

from backend.persistence.native.store import VectoraStore
from backend.storage.sqlite.pool import AsyncConnectionPool


@pytest.fixture
async def store(tmp_path):
    """Executa a operação store."""
    pool = AsyncConnectionPool(str(tmp_path / "store.db"), min_size=1, max_size=2)
    await pool.open()
    store = VectoraStore(pool)
    await store.setup()
    try:
        yield store
    finally:
        await pool.close()


async def _fake_embed(texts: list[str]) -> list[list[float]]:
    """Embedding determinístico só pra exercitar o caminho semântico sem
    depender de um provider real — dimensão fixa de 3."""
    return [[float(len(t)), float(len(t) % 3), 1.0] for t in texts]


@pytest.fixture
async def indexed_store(tmp_path):
    """Executa a operação indexed store."""
    pool = AsyncConnectionPool(
        str(tmp_path / "store_indexed.db"), min_size=1, max_size=2
    )
    await pool.open()
    store = VectoraStore(
        pool, index={"dims": 3, "embed": _fake_embed, "fields": ["text"]}
    )
    await store.setup()
    try:
        yield store
    finally:
        await pool.close()


class TestPutGet:
    async def test_purge_expired_pagina_por_chave_acima_de_um_lote(
        self, store: VectoraStore
    ) -> None:
        """Expirados intercalados não fazem válidos sumirem nem repetem a página."""
        namespace = ("user", "batch", "memories")
        for index in range(240):
            await store.aput(
                namespace,
                f"k-{index:03d}",
                {
                    "content": str(index),
                    "valid_until": (
                        "2000-01-01T00:00:00+00:00" if index % 2 == 0 else None
                    ),
                },
            )

        assert await store.purge_expired() == 120
        remaining = await store.asearch(namespace, limit=300)
        assert len(remaining) == 120
        assert all(int(item.key[2:]) % 2 == 1 for item in remaining)

    async def test_purge_expired_remove_registros_fisicamente(
        self, store: VectoraStore
    ):
        """A manutenção não deixa registros expirados acumularem no banco."""
        await store.aput(
            ("user", "u1", "memories"),
            "expired",
            {"content": "não usar", "valid_until": "2000-01-01T00:00:00+00:00"},
        )
        await store.aput(("user", "u1", "memories"), "live", {"content": "usar"})

        assert await store.purge_expired() == 1
        assert await store.aget(("user", "u1", "memories"), "expired") is None
        assert await store.aget(("user", "u1", "memories"), "live") is not None

    async def test_item_expirado_nao_e_reprojetado(self, store: VectoraStore):
        """Verifica o comportamento de item expirado nao e reprojetado."""
        await store.aput(
            ("user", "u1", "memories"),
            "expired",
            {"content": "não usar", "valid_until": "2000-01-01T00:00:00+00:00"},
        )

        assert await store.aget(("user", "u1", "memories"), "expired") is None
        assert await store.asearch(("user", "u1", "memories")) == []

    async def test_timestamp_sem_fuso_nao_interrompe_leitura(self, store: VectoraStore):
        """Verifica o comportamento de timestamp sem fuso nao interrompe leitura."""
        await store.aput(
            ("user", "u1", "memories"),
            "legacy",
            {"content": "expirado", "valid_until": "2000-01-01T00:00:00"},
        )
        await store.aput(("user", "u1", "memories"), "live", {"content": "válido"})

        assert await store.aget(("user", "u1", "memories"), "legacy") is None
        results = await store.asearch(("user", "u1", "memories"))
        assert [item.key for item in results] == ["live"]

    async def test_round_trip_grava_e_le_um_item(self, store: VectoraStore):
        """Verifica o comportamento de round trip grava e le um item."""
        await store.aput(("user", "u1", "memories"), "k1", {"fact": "gosta de café"})

        result = await store.aget(("user", "u1", "memories"), "k1")

        assert result is not None
        assert result.value == {"fact": "gosta de café"}

    async def test_chave_inexistente_retorna_none(self, store: VectoraStore):
        """Verifica o comportamento de chave inexistente retorna none."""
        result = await store.aget(("user", "u1", "memories"), "ausente")

        assert result is None

    async def test_delete_remove_o_item(self, store: VectoraStore):
        """Verifica o comportamento de delete remove o item."""
        await store.aput(("user", "u1", "memories"), "k1", {"fact": "x"})
        await store.adelete(("user", "u1", "memories"), "k1")

        result = await store.aget(("user", "u1", "memories"), "k1")
        assert result is None

    async def test_delete_de_chave_inexistente_nao_levanta(self, store: VectoraStore):
        """Verifica o comportamento de delete de chave inexistente nao levanta."""
        await store.adelete(("user", "u1", "memories"), "ausente")

    async def test_isolamento_entre_namespaces_de_usuarios_diferentes(
        self, store: VectoraStore
    ):
        """Verifica o comportamento de isolamento entre namespaces de usuarios diferentes."""
        await store.aput(("user", "alice", "memories"), "k1", {"fact": "a"})
        await store.aput(("user", "bob", "memories"), "k1", {"fact": "b"})

        alice_item = await store.aget(("user", "alice", "memories"), "k1")
        assert alice_item is not None
        assert alice_item.value == {"fact": "a"}


class TestSearch:
    async def test_search_filtra_por_prefixo_de_namespace(self, store: VectoraStore):
        """Verifica o comportamento de search filtra por prefixo de namespace."""
        await store.aput(("user", "u1", "memories"), "k1", {"fact": "x"})
        await store.aput(("user", "u1", "prefs"), "k1", {"pref": "y"})

        results = await store.asearch(("user", "u1", "memories"))

        assert len(results) == 1
        assert results[0].value == {"fact": "x"}

    async def test_search_sem_resultado_devolve_lista_vazia(self, store: VectoraStore):
        """Verifica o comportamento de search sem resultado devolve lista vazia."""
        results = await store.asearch(("user", "inexistente", "memories"))

        assert results == []

    async def test_search_filtra_por_valor(self, store: VectoraStore):
        """Verifica o comportamento de search filtra por valor."""
        await store.aput(("user", "u1", "memories"), "k1", {"category": "food"})
        await store.aput(("user", "u1", "memories"), "k2", {"category": "travel"})

        results = await store.asearch(
            ("user", "u1", "memories"), filter={"category": "food"}
        )

        assert len(results) == 1
        assert results[0].key == "k1"

    async def test_search_semantico_ordena_por_similaridade(
        self, indexed_store: VectoraStore
    ):
        """Verifica o comportamento de search semantico ordena por similaridade."""
        await indexed_store.aput(("user", "u1", "memories"), "curto", {"text": "ab"})
        await indexed_store.aput(
            ("user", "u1", "memories"), "longo", {"text": "abcdefgh"}
        )

        results = await indexed_store.asearch(
            ("user", "u1", "memories"), query="abcdefgh"
        )

        assert results[0].key == "longo"
        assert results[0].score is not None
