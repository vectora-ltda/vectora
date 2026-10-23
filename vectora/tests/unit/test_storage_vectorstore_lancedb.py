"""Testes unitários de `LanceDBBackend.search_text` — mocka `lancedb.connect_async`
pra pegar bug estrutural (nome de parâmetro errado, parsing de forma de
resposta errada) sem depender de um banco LanceDB real em disco."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import lancedb
import numpy as np
import pandas as pd
import pytest

from backend.storage.vectorstore.base import VectorRow
from backend.storage.vectorstore.lancedb_backend import LanceDBBackend

from ._lancedb_fakes import fake_async_connection, fake_async_table


@pytest.fixture
def backend():
    return LanceDBBackend(lancedb_dir="/tmp/fake-lancedb")


@pytest.fixture
def mock_db():
    db = fake_async_connection()
    with patch(
        "backend.storage.vectorstore.lancedb_backend.lancedb.connect_async",
        AsyncMock(return_value=db),
    ):
        yield db


def _fts_query(df: pd.DataFrame) -> MagicMock:
    """Molde do objeto retornado por `await table.search(query, query_type="fts")`
    — `table.search()` é async (diferente de `vector_search()`/`query()`,
    que são síncronos); o query builder resultante tem `.limit()` síncrono
    e encadeável, com `.to_pandas()` async."""
    query_obj = MagicMock()
    query_obj.limit.return_value.to_pandas = AsyncMock(return_value=df)
    return query_obj


class TestLanceDBBackendSearchText:
    @pytest.mark.asyncio
    async def test_search_text_retorna_hits_do_indice_fts(self, backend, mock_db):
        table = fake_async_table()
        table.create_index = AsyncMock()
        df = pd.DataFrame(
            [
                {
                    "id": "doc-1",
                    "_score": 3.5,
                    "text": "erro de autenticação JWT",
                    "metadata": "{}",
                }
            ]
        )
        table.search = AsyncMock(return_value=_fts_query(df))
        mock_db.open_table.return_value = table

        hits = await backend.search_text("articles", "JWT", limit=5)

        assert len(hits) == 1
        assert hits[0].id == "doc-1"
        assert hits[0].score == pytest.approx(3.5)
        assert hits[0].content == "erro de autenticação JWT"
        table.create_index.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_search_text_colecao_inexistente_retorna_vazio_sem_propagar(
        self, backend, mock_db
    ):
        mock_db.open_table.side_effect = RuntimeError("tabela não existe")

        hits = await backend.search_text("nao-existe", "query", limit=5)

        assert hits == []

    @pytest.mark.asyncio
    async def test_search_text_create_index_falhando_ainda_tenta_buscar(
        self, backend, mock_db
    ):
        """`create_index` falha quando o índice já existe (comportamento
        normal em rodadas subsequentes) — não pode impedir a busca."""
        table = fake_async_table()
        table.create_index = AsyncMock(side_effect=RuntimeError("índice já existe"))
        df = pd.DataFrame(
            [{"id": "doc-1", "_score": 1.0, "text": "conteúdo", "metadata": "{}"}]
        )
        table.search = AsyncMock(return_value=_fts_query(df))
        mock_db.open_table.return_value = table

        hits = await backend.search_text("articles", "conteúdo", limit=5)

        assert len(hits) == 1

    @pytest.mark.asyncio
    async def test_search_text_fts_indisponivel_retorna_vazio_sem_propagar(
        self, backend, mock_db
    ):
        table = fake_async_table()
        table.create_index = AsyncMock()
        table.search = AsyncMock(side_effect=RuntimeError("FTS não suportado"))
        mock_db.open_table.return_value = table

        hits = await backend.search_text("articles", "query", limit=5)

        assert hits == []


class TestLanceDBBackendSearchTextRealTable:
    """Regressão ao vivo: faltava `await` antes de `.limit()` em
    `table.search(...)` — `AsyncTable.search()` é async, diferente de
    `vector_search()`/`query()`. Os mocks acima já pegam isso, mas só uma
    tabela real confirma que a busca FTS retorna resultados de verdade.

    Cria índice FTS real via `create_fts_index()`/`table.create_index()`
    — trava de forma determinística em CI (Linux), ver comentário
    completo em pyproject.toml (seção `timeout`). Isolado em step
    separado do workflow via marcador `real_lancedb`."""

    pytestmark = pytest.mark.real_lancedb

    async def test_busca_fts_retorna_hits_reais_por_termo_textual(
        self, tmp_path
    ) -> None:
        backend = LanceDBBackend(lancedb_dir=str(tmp_path))
        db = await backend._db()
        await db.create_table(
            "articles",
            data=[
                {
                    "id": "doc-1",
                    "vector": [0.1, 0.2, 0.3, 0.4],
                    "text": "erro de autenticacao JWT expirado",
                    "metadata": "{}",
                },
                {
                    "id": "doc-2",
                    "vector": [0.9, 0.8, 0.7, 0.6],
                    "text": "receita de bolo de chocolate",
                    "metadata": "{}",
                },
            ],
        )

        hits = await backend.search_text("articles", "autenticacao", limit=5)

        assert len(hits) == 1
        assert hits[0].id == "doc-1"


class TestLanceDBBackendListRows:
    """Regressão ao vivo: `list_rows` derrubava o resumo RAG do workspace
    toda vez que uma linha tinha vetor de verdade — `row.get("vector")`
    devolve `numpy.ndarray`, e `array or []` explode com "truth value of
    an array with more than one element is ambiguous"."""

    @pytest.mark.asyncio
    async def test_linha_com_vetor_multi_elemento_nao_lanca(self, backend, mock_db):
        table = fake_async_table()
        df = pd.DataFrame(
            [
                {
                    "id": "doc-1",
                    "vector": np.array([0.1, 0.2, 0.3]),
                    "text": "conteúdo",
                    "metadata": "{}",
                }
            ]
        )
        table.to_pandas = AsyncMock(return_value=df)
        mock_db.open_table = AsyncMock(return_value=table)

        rows = await backend.list_rows("articles")

        assert len(rows) == 1
        assert rows[0].vector == pytest.approx([0.1, 0.2, 0.3])

    @pytest.mark.asyncio
    async def test_linha_sem_vetor_vira_lista_vazia(self, backend, mock_db):
        """Coluna `vector` ausente na linha vem como escalar `NaN` (float)
        do pandas, não `None` — `list(NaN)` também lançaria sem o guard."""
        table = fake_async_table()
        df = pd.DataFrame(
            [
                {
                    "id": "doc-2",
                    "vector": float("nan"),
                    "text": "sem vetor",
                    "metadata": "{}",
                }
            ]
        )
        table.to_pandas = AsyncMock(return_value=df)
        mock_db.open_table = AsyncMock(return_value=table)

        rows = await backend.list_rows("articles")

        assert rows[0].vector == []

    @pytest.mark.asyncio
    async def test_colecao_inexistente_retorna_vazio_sem_propagar(
        self, backend, mock_db
    ):
        mock_db.open_table = AsyncMock(side_effect=RuntimeError("tabela não existe"))

        rows = await backend.list_rows("nao-existe")

        assert rows == []


class TestLanceDBBackendUpsert:
    @pytest.mark.asyncio
    async def test_upsert_remove_ids_duplicados_legados_antes_do_merge(
        self, backend: LanceDBBackend, mock_db: lancedb.AsyncConnection
    ) -> None:
        table = fake_async_table()
        table.to_pandas = AsyncMock(
            return_value=pd.DataFrame(
                [
                    {
                        "id": "legacy",
                        "vector": [0.1, 0.2],
                        "text": "primeiro",
                        "metadata": "{}",
                    },
                    {
                        "id": "legacy",
                        "vector": [0.3, 0.4],
                        "text": "último",
                        "metadata": '{"version": 2}',
                    },
                    {
                        "id": "stable",
                        "vector": [0.5, 0.6],
                        "text": "único",
                        "metadata": "{}",
                    },
                ]
            )
        )
        query_mock = cast(AsyncMock, table.query)
        query_mock.return_value.select.return_value.to_pandas = AsyncMock(
            return_value=pd.DataFrame(
                [{"id": "legacy"}, {"id": "legacy"}, {"id": "stable"}]
            )
        )
        table.delete = AsyncMock()
        merge = MagicMock()
        merge.when_matched_update_all.return_value = merge
        merge.when_not_matched_insert_all.return_value = merge
        merge.execute = AsyncMock()
        merge_insert = MagicMock(return_value=merge)
        table.merge_insert = merge_insert  # type: ignore[assignment]
        cast(AsyncMock, mock_db.open_table).return_value = table

        await backend.upsert(
            "articles",
            [VectorRow(id="fresh", vector=[0.7, 0.8], text="novo", metadata={})],
        )

        table.delete.assert_awaited_once_with("id = 'legacy'")
        assert cast(AsyncMock, table.add).await_count == 0
        merge_insert.assert_called_once_with("id")
        merge.execute.assert_awaited_once()
        execute_mock = merge.execute
        assert execute_mock.await_args is not None
        docs = execute_mock.await_args.args[0]
        assert {doc["id"] for doc in docs} == {"legacy", "fresh"}
