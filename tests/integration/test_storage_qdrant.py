"""Testes de integração — Qdrant (conectividade, coleções, busca vetorial).

Requer Qdrant rodando (vectora-qdrant via docker).
Os fixtures de conftest.py sobem o container automaticamente se Docker estiver
disponível; do contrário, todos os testes são pulados.
"""

from __future__ import annotations

import contextlib

import pytest


class TestQdrantConnectivity:
    """Conectividade básica ao Qdrant."""

    @pytest.mark.storage
    def test_health_endpoint(self, qdrant_client):
        """Qdrant responde ao health check (list_collections bem-sucedido)."""
        result = qdrant_client.get_collections()
        assert hasattr(result, "collections")

    @pytest.mark.storage
    def test_list_collections(self, qdrant_client):
        """list_collections() retorna lista (pode estar vazia)."""
        collections = qdrant_client.get_collections()
        assert hasattr(collections, "collections")

    @pytest.mark.storage
    def test_create_and_delete_collection(self, qdrant_client):
        """Cria e deleta coleção de teste."""
        from qdrant_client.models import Distance, VectorParams

        name = "_test_vectora_integration"
        with contextlib.suppress(Exception):
            qdrant_client.delete_collection(name)

        qdrant_client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=4, distance=Distance.COSINE),
        )

        collections = [c.name for c in qdrant_client.get_collections().collections]
        assert name in collections

        qdrant_client.delete_collection(name)
        collections_after = [
            c.name for c in qdrant_client.get_collections().collections
        ]
        assert name not in collections_after

    @pytest.mark.storage
    def test_upsert_and_search(self, qdrant_client):
        """Insere vetores e recupera por busca de similaridade."""
        from qdrant_client.models import Distance, PointStruct, VectorParams

        name = "_test_vectora_search"
        with contextlib.suppress(Exception):
            qdrant_client.delete_collection(name)

        qdrant_client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=4, distance=Distance.COSINE),
        )

        qdrant_client.upsert(
            collection_name=name,
            points=[
                PointStruct(id=1, vector=[1.0, 0.0, 0.0, 0.0], payload={"text": "a"}),
                PointStruct(id=2, vector=[0.0, 1.0, 0.0, 0.0], payload={"text": "b"}),
                PointStruct(id=3, vector=[0.0, 0.0, 1.0, 0.0], payload={"text": "c"}),
            ],
        )

        response = qdrant_client.query_points(
            collection_name=name,
            query=[1.0, 0.0, 0.0, 0.0],
            limit=1,
        )
        assert len(response.points) == 1
        assert response.points[0].id == 1

        qdrant_client.delete_collection(name)

    @pytest.mark.storage
    def test_search_error_wrong_dimension(self, qdrant_client):
        """Erro: busca com vetor de dimensão errada levanta exceção."""
        from qdrant_client.models import Distance, VectorParams

        name = "_test_dim_error"
        with contextlib.suppress(Exception):
            qdrant_client.delete_collection(name)

        qdrant_client.create_collection(
            collection_name=name,
            vectors_config=VectorParams(size=4, distance=Distance.COSINE),
        )

        from qdrant_client.http.exceptions import UnexpectedResponse

        with pytest.raises(UnexpectedResponse):
            qdrant_client.query_points(
                collection_name=name,
                query=[1.0, 0.0],  # dimensão errada (2 em vez de 4)
                limit=1,
            )

        qdrant_client.delete_collection(name)


class TestQdrantBackendNative:
    """Round-trip real do `QdrantBackend` (backend/storage/vectorstore) —
    cliente nativo `AsyncQdrantClient`."""

    @pytest.fixture
    def backend(self, _storage_stack_ok, qdrant_url):
        if not _storage_stack_ok:
            pytest.skip("Docker indisponível — Qdrant não iniciado")

        from backend.storage.vectorstore.qdrant_backend import QdrantBackend

        return QdrantBackend(url=qdrant_url, api_key="vectora")

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_upsert_e_search_roundtrip(self, backend, qdrant_client):
        from backend.storage.vectorstore.base import VectorRow

        name = "_test_backend_search"
        with contextlib.suppress(Exception):
            qdrant_client.delete_collection(name)

        await backend.upsert(
            name,
            [
                VectorRow(
                    id="a",
                    vector=[1.0, 0.0, 0.0, 0.0],
                    text="doc a",
                    metadata={"k": "v"},
                ),
                VectorRow(
                    id="b", vector=[0.0, 1.0, 0.0, 0.0], text="doc b", metadata={}
                ),
            ],
        )

        hits = await backend.search(name, [1.0, 0.0, 0.0, 0.0], limit=1)
        assert len(hits) == 1
        assert hits[0].id == "a"
        assert hits[0].content == "doc a"
        assert hits[0].metadata == {"k": "v"}
        # Score é normalizado como "distância" (0 = idêntico) — consistente
        # com a convenção do LanceDB, não a similaridade cosine crua do Qdrant.
        assert hits[0].score < 0.01

        qdrant_client.delete_collection(name)

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_search_colecao_ausente_devolve_lista_vazia(self, backend):
        """Erro/borda: coleção que nunca foi criada nunca lança — devolve []."""
        hits = await backend.search("_never_created_xyz", [1.0, 0.0], limit=5)
        assert hits == []

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_list_rows_e_delete(self, backend, qdrant_client):
        from backend.storage.vectorstore.base import VectorRow

        name = "_test_backend_list_delete"
        with contextlib.suppress(Exception):
            qdrant_client.delete_collection(name)

        await backend.upsert(
            name,
            [
                VectorRow(
                    id="1", vector=[0.1, 0.2], text="um", metadata={"source": "x"}
                ),
                VectorRow(
                    id="2", vector=[0.3, 0.4], text="dois", metadata={"source": "y"}
                ),
            ],
        )

        rows = await backend.list_rows(name)
        assert {r.id for r in rows} == {"1", "2"}

        deleted = await backend.delete(name, ["1"])
        assert deleted == 1
        remaining = await backend.list_rows(name)
        assert {r.id for r in remaining} == {"2"}

        qdrant_client.delete_collection(name)

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_purge_e_list_collections(self, backend, qdrant_client):
        from backend.storage.vectorstore.base import VectorRow

        name = "_test_backend_purge"
        with contextlib.suppress(Exception):
            qdrant_client.delete_collection(name)

        await backend.upsert(
            name, [VectorRow(id="1", vector=[0.1], text="x", metadata={})]
        )
        names = await backend.list_collections()
        assert name in names

        await backend.purge(name)
        names_after = await backend.list_collections()
        assert name not in names_after

        # Erro/borda: purge de coleção já apagada não levanta (idempotente).
        await backend.purge(name)

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_count(self, backend, qdrant_client):
        from backend.storage.vectorstore.base import VectorRow

        name = "_test_backend_count"
        with contextlib.suppress(Exception):
            qdrant_client.delete_collection(name)

        await backend.upsert(
            name,
            [
                VectorRow(id="1", vector=[0.1], text="a", metadata={}),
                VectorRow(id="2", vector=[0.2], text="b", metadata={}),
            ],
        )
        assert await backend.count(name) == 2
        # Erro/borda: coleção inexistente devolve None, não levanta.
        assert await backend.count("_never_created_count") is None

        qdrant_client.delete_collection(name)


class TestVectorStoreRoutingRealStack:
    """`get_vector_store_backend()` roteia lite→LanceDB, complete→Qdrant —
    contra o storage real (docker), não mock."""

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_storage_mode_complete_roteia_para_qdrant(
        self, _storage_stack_ok, monkeypatch
    ):
        if not _storage_stack_ok:
            pytest.skip("Docker indisponível — Qdrant não iniciado")

        from backend.storage import factory
        from backend.storage.vectorstore.qdrant_backend import QdrantBackend

        monkeypatch.setattr(
            "backend.services.license.get_effective_storage_mode", lambda: "complete"
        )
        factory._reset_singletons()
        try:
            backend = await factory.get_vector_store_backend()
            assert isinstance(backend, QdrantBackend)
        finally:
            factory._reset_singletons()

    @pytest.mark.asyncio
    @pytest.mark.storage
    async def test_storage_mode_lite_roteia_para_lancedb(self, monkeypatch):
        from backend.storage import factory
        from backend.storage.vectorstore.lancedb_backend import LanceDBBackend

        monkeypatch.setattr(
            "backend.services.license.get_effective_storage_mode", lambda: "lite"
        )
        factory._reset_singletons()
        try:
            backend = await factory.get_vector_store_backend()
            assert isinstance(backend, LanceDBBackend)
        finally:
            factory._reset_singletons()
