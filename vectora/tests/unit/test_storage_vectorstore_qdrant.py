"""Testes unitários de `QdrantBackend` — mocka `AsyncQdrantClient` pra pegar
bug estrutural (nome de parâmetro errado, parsing de forma de resposta
errada) sem depender de um Qdrant real rodando (isso é coberto pelos
testes reais em `tests/integration/test_storage_qdrant.py`)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from qdrant_client.http.models import (
    CollectionDescription,
    CollectionsResponse,
    CountResult,
    QueryResponse,
    Record,
    ScoredPoint,
)

from backend.storage.vectorstore.base import VectorRow
from backend.storage.vectorstore.qdrant_backend import QdrantBackend, _point_id


@pytest.fixture
def backend():
    """Executa a operação backend."""
    return QdrantBackend(url="http://localhost:6333")


@pytest.fixture
def mock_client():
    """Executa a operação mock client."""
    with patch("backend.storage.vectorstore.qdrant_backend.AsyncQdrantClient") as cls:
        instance = AsyncMock()
        cls.return_value = instance
        yield instance


class TestQdrantBackendSearch:
    @pytest.mark.asyncio
    async def test_search_converte_score_em_distancia_e_parseia_payload(
        self, backend, mock_client
    ):
        """Verifica o comportamento de search converte score em distancia e parseia payload."""
        mock_client.collection_exists.return_value = True
        mock_client.query_points.return_value = QueryResponse(
            points=[
                ScoredPoint(
                    id="p1",
                    version=1,
                    score=0.9,
                    payload={"text": "conteúdo", "metadata": {"workspace_id": "w1"}},
                )
            ]
        )

        hits = await backend.search("articles", [0.1, 0.2], limit=5)

        assert len(hits) == 1
        assert hits[0].id == "p1"
        assert hits[0].score == pytest.approx(1.0 - 0.9)
        assert hits[0].content == "conteúdo"
        assert hits[0].metadata == {"workspace_id": "w1"}

    @pytest.mark.asyncio
    async def test_search_colecao_inexistente_retorna_vazio_sem_propagar(
        self, backend, mock_client
    ):
        """Verifica o comportamento de search colecao inexistente retorna vazio sem propagar."""
        mock_client.collection_exists.return_value = False

        hits = await backend.search("nao-existe", [0.1], limit=5)

        assert hits == []
        mock_client.query_points.assert_not_called()


class TestQdrantBackendSearchText:
    @pytest.mark.asyncio
    async def test_search_text_rankeia_por_overlap_de_termos(
        self, backend, mock_client
    ):
        """Verifica o comportamento de search text rankeia por overlap de termos."""
        mock_client.collection_exists.return_value = True
        mock_client.scroll.return_value = (
            [
                Record(
                    id="p-fraco",
                    payload={"text": "menciona auth uma vez", "metadata": {}},
                ),
                Record(
                    id="p-forte",
                    payload={
                        "text": "auth auth auth — tudo sobre auth aqui",
                        "metadata": {},
                    },
                ),
            ],
            None,
        )

        hits = await backend.search_text("articles", "auth", limit=5)

        assert [h.id for h in hits] == ["p-forte", "p-fraco"]
        assert hits[0].score > hits[1].score

    @pytest.mark.asyncio
    async def test_search_text_colecao_inexistente_retorna_vazio_sem_propagar(
        self, backend, mock_client
    ):
        """Verifica o comportamento de search text colecao inexistente retorna vazio sem propagar."""
        mock_client.collection_exists.return_value = False

        hits = await backend.search_text("nao-existe", "auth", limit=5)

        assert hits == []
        mock_client.scroll.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_text_erro_no_scroll_retorna_vazio_sem_propagar(
        self, backend, mock_client
    ):
        """Verifica o comportamento de search text erro no scroll retorna vazio sem propagar."""
        mock_client.collection_exists.return_value = True
        mock_client.scroll.side_effect = RuntimeError("qdrant indisponível")

        hits = await backend.search_text("articles", "auth", limit=5)

        assert hits == []

    @pytest.mark.asyncio
    async def test_search_text_create_index_falhando_nao_impede_busca(
        self, backend, mock_client
    ):
        """Verifica o comportamento de search text create index falhando nao impede busca."""
        mock_client.collection_exists.return_value = True
        mock_client.create_payload_index.side_effect = RuntimeError("índice já existe")
        mock_client.scroll.return_value = (
            [Record(id="p1", payload={"text": "auth", "metadata": {}})],
            None,
        )

        hits = await backend.search_text("articles", "auth", limit=5)

        assert len(hits) == 1
        assert hits[0].id == "p1"


class TestQdrantBackendUpsert:
    @pytest.mark.asyncio
    async def test_upsert_cria_colecao_e_envia_pontos(self, backend, mock_client):
        """Verifica o comportamento de upsert cria colecao e envia pontos."""
        mock_client.collection_exists.return_value = False

        await backend.upsert(
            "articles",
            [VectorRow(id="p1", vector=[0.1, 0.2], text="a", metadata={"k": "v"})],
        )

        mock_client.create_collection.assert_awaited_once()
        mock_client.upsert.assert_awaited_once()
        _, kwargs = mock_client.upsert.call_args
        assert kwargs["collection_name"] == "articles"
        assert kwargs["points"][0].id == _point_id("articles", "p1")

    @pytest.mark.asyncio
    async def test_upsert_lista_vazia_nao_chama_cliente(self, backend, mock_client):
        """Verifica o comportamento de upsert lista vazia nao chama cliente."""
        await backend.upsert("articles", [])

        mock_client.create_collection.assert_not_called()
        mock_client.upsert.assert_not_called()


class TestQdrantBackendListRows:
    @pytest.mark.asyncio
    async def test_list_rows_pagina_via_scroll_ate_offset_none(
        self, backend, mock_client
    ):
        """Verifica o comportamento de list rows pagina via scroll ate offset none."""
        mock_client.collection_exists.return_value = True
        page1 = [
            Record(
                id="p1",
                payload={"text": "a", "metadata": {}},
                vector=[0.1, 0.2],
            )
        ]
        page2 = [
            Record(
                id="p2",
                payload={"text": "b", "metadata": {}},
                vector=[0.3, 0.4],
            )
        ]
        mock_client.scroll.side_effect = [(page1, "next-offset"), (page2, None)]

        rows = await backend.list_rows("articles")

        assert [r.id for r in rows] == ["p1", "p2"]
        assert mock_client.scroll.await_count == 2

    @pytest.mark.asyncio
    async def test_list_rows_colecao_inexistente_retorna_vazio(
        self, backend, mock_client
    ):
        """Verifica o comportamento de list rows colecao inexistente retorna vazio."""
        mock_client.collection_exists.return_value = False

        rows = await backend.list_rows("nao-existe")

        assert rows == []
        mock_client.scroll.assert_not_called()


class TestQdrantBackendDelete:
    @pytest.mark.asyncio
    async def test_delete_envia_point_ids_list(self, backend, mock_client):
        """Verifica o comportamento de delete envia point ids list."""
        mock_client.count.return_value = CountResult(count=2)
        count = await backend.delete("articles", ["p1", "p2"])

        assert count == 2
        count_kwargs = mock_client.count.call_args.kwargs
        assert count_kwargs["collection_name"] == "articles"
        assert count_kwargs["exact"] is True
        assert count_kwargs["count_filter"].must[0].has_id == [
            _point_id("articles", "p1"),
            _point_id("articles", "p2"),
        ]

        _, kwargs = mock_client.delete.call_args
        assert kwargs["collection_name"] == "articles"
        assert kwargs["points_selector"].points == [
            _point_id("articles", "p1"),
            _point_id("articles", "p2"),
        ]
        assert kwargs["wait"] is True

    @pytest.mark.asyncio
    async def test_delete_concorrente_coordena_exclusoes(
        self, backend: QdrantBackend, mock_client: AsyncMock
    ) -> None:
        """Verifica o comportamento de delete concorrente coordena exclusoes."""
        count = 1

        async def fake_count(*args: object, **kwargs: object) -> CountResult:
            """Executa a operação fake count."""
            await asyncio.sleep(0)
            return CountResult(count=count)

        async def fake_delete(*args: object, **kwargs: object) -> None:
            """Executa a operação fake delete."""
            nonlocal count
            await asyncio.sleep(0)
            count = 0

        mock_client.count.side_effect = fake_count
        mock_client.delete.side_effect = fake_delete
        results = await asyncio.gather(
            backend.delete("articles", ["p1"]),
            backend.delete("articles", ["p1"]),
        )
        assert sorted(results) == [0, 1]
        mock_client.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_lista_vazia_nao_chama_cliente(self, backend, mock_client):
        """Verifica o comportamento de delete lista vazia nao chama cliente."""
        count = await backend.delete("articles", [])

        assert count == 0
        mock_client.delete.assert_not_called()


class TestQdrantBackendCollections:
    @pytest.mark.asyncio
    async def test_list_collections_extrai_nomes(self, backend, mock_client):
        """Verifica o comportamento de list collections extrai nomes."""
        mock_client.get_collections.return_value = CollectionsResponse(
            collections=[
                CollectionDescription(name="articles"),
                CollectionDescription(name="notes"),
            ]
        )

        names = await backend.list_collections()

        assert names == ["articles", "notes"]

    @pytest.mark.asyncio
    async def test_list_collections_erro_retorna_vazio_sem_propagar(
        self, backend, mock_client
    ):
        """Verifica o comportamento de list collections erro retorna vazio sem propagar."""
        mock_client.get_collections.side_effect = RuntimeError("conexão recusada")

        names = await backend.list_collections()

        assert names == []


class TestQdrantBackendCount:
    @pytest.mark.asyncio
    async def test_count_retorna_valor_exato(self, backend, mock_client):
        """Verifica o comportamento de count retorna valor exato."""
        mock_client.count.return_value = CountResult(count=42)

        result = await backend.count("articles")

        assert result == 42
        _, kwargs = mock_client.count.call_args
        assert kwargs["exact"] is True

    @pytest.mark.asyncio
    async def test_count_erro_retorna_none_sem_propagar(self, backend, mock_client):
        """Verifica o comportamento de count erro retorna none sem propagar."""
        mock_client.count.side_effect = RuntimeError("timeout")

        result = await backend.count("articles")

        assert result is None


class TestQdrantBackendPurge:
    @pytest.mark.asyncio
    async def test_purge_apaga_colecao(self, backend, mock_client):
        """Verifica o comportamento de purge apaga colecao."""
        await backend.purge("articles")

        mock_client.delete_collection.assert_awaited_once_with("articles")

    @pytest.mark.asyncio
    async def test_purge_idempotente_em_colecao_inexistente(self, backend, mock_client):
        """Verifica o comportamento de purge idempotente em colecao inexistente."""
        mock_client.delete_collection.side_effect = RuntimeError("not found")

        await backend.purge("nao-existe")
