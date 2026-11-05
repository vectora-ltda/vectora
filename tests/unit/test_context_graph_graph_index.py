"""Testes para backend/services/context_graph/graph_index.py (GraphRAG)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.context_graph.graph_index import (
    _node_text,
    index_graph_nodes,
    purge_graph_index,
    search_graph_nodes,
)

_GETDB = "backend.context_graph.graph_index._get_db"
_EMBED = "backend.context_graph.graph_index._embed_texts"

_GRAPH_DATA = {
    "nodes": [
        {
            "id": "n.auth",
            "label": "AuthService",
            "file_type": "code",
            "source_file": "auth.py",
            "docstring": "Handles authentication.",
        },
        {
            "id": "n.token",
            "label": "TokenHandler",
            "file_type": "code",
            "source_file": "token.py",
        },
        {
            "id": "n.db",
            "label": "Database",
            "file_type": "code",
            "source_file": "db.py",
        },
    ],
    "edges": [],
}

_VEC3 = [[0.1] * 10, [0.2] * 10, [0.3] * 10]


def _mock_lancedb():
    """Mock do LanceDB para testes sem conexão real."""
    db = AsyncMock()
    table = AsyncMock()
    table.count_rows = AsyncMock(return_value=0)
    db.table_names = AsyncMock(return_value=[])
    db.create_table = AsyncMock(return_value=table)
    db.open_table = AsyncMock(return_value=table)
    db.drop_table = AsyncMock()
    return db


def _df(rows: list[tuple[int, dict]]) -> MagicMock:
    """Fake DataFrame com iterrows()."""
    df = MagicMock()
    df.iterrows.return_value = iter(rows)
    return df


def _search_table(db, df: MagicMock):
    # vector_search().limit().to_pandas() é encadeamento SÍNCRONO terminando num
    # await — table precisa ser MagicMock (não AsyncMock) com to_pandas async.
    table = MagicMock()
    table.vector_search.return_value.limit.return_value.to_pandas = AsyncMock(
        return_value=df
    )
    db.open_table = AsyncMock(return_value=table)
    db.table_names = AsyncMock(return_value=["context_graph_nodes"])
    return table


# ---------------------------------------------------------------------------
# _node_text — texto canônico de um nó
# ---------------------------------------------------------------------------


class TestNodeText:
    def test_label_only(self) -> None:
        assert _node_text({"id": "x", "label": "Foo"}) == "Foo"

    def test_fallback_to_id_when_no_label(self) -> None:
        assert _node_text({"id": "x.y"}) == "x.y"

    def test_includes_file_type(self) -> None:
        assert "— code" in _node_text({"label": "Foo", "file_type": "code"})

    def test_includes_source_file(self) -> None:
        assert "em a.py" in _node_text({"label": "Foo", "source_file": "a.py"})

    def test_includes_docstring(self) -> None:
        assert ". Does X" in _node_text({"label": "Foo", "docstring": "Does X"})

    def test_docstring_truncated_to_200(self) -> None:
        out = _node_text({"label": "F", "docstring": "x" * 500})
        assert out.count("x") == 200

    def test_full_node_has_all_parts(self) -> None:
        out = _node_text(
            {
                "label": "Auth",
                "file_type": "class",
                "source_file": "a.py",
                "docstring": "D",
            }
        )
        assert "Auth" in out and "class" in out and "a.py" in out and "D" in out

    def test_non_string_docstring_coerced(self) -> None:
        assert "123" in _node_text({"label": "F", "docstring": 123})

    def test_empty_node_is_empty_string(self) -> None:
        assert _node_text({}) == ""


# ---------------------------------------------------------------------------
# index_graph_nodes
# ---------------------------------------------------------------------------


class TestIndexGraphNodes:
    @pytest.mark.asyncio
    async def test_empty_graph_indexes_zero(self) -> None:
        db = _mock_lancedb()
        with (
            patch(_GETDB, new_callable=AsyncMock, return_value=db),
            patch(_EMBED, new_callable=AsyncMock, return_value=[]),
        ):
            assert await index_graph_nodes("ws-1", {"nodes": [], "edges": []}) == 0

    @pytest.mark.asyncio
    async def test_three_nodes_indexes_three(self) -> None:
        db = _mock_lancedb()
        with (
            patch(_GETDB, new_callable=AsyncMock, return_value=db),
            patch(_EMBED, new_callable=AsyncMock, return_value=_VEC3),
        ):
            assert await index_graph_nodes("ws-1", _GRAPH_DATA) == 3

    @pytest.mark.asyncio
    async def test_node_without_docstring_does_not_break(self) -> None:
        db = _mock_lancedb()
        data = {
            "nodes": [{"id": "n.x", "label": "X", "file_type": "code"}],
            "edges": [],
        }
        with (
            patch(_GETDB, new_callable=AsyncMock, return_value=db),
            patch(_EMBED, new_callable=AsyncMock, return_value=[[0.5] * 10]),
        ):
            assert await index_graph_nodes("ws-1", data) == 1

    @pytest.mark.asyncio
    async def test_embeddings_unavailable_returns_zero(self) -> None:
        db = _mock_lancedb()
        with (
            patch(_GETDB, new_callable=AsyncMock, return_value=db),
            patch(_EMBED, new_callable=AsyncMock, return_value=[]),
        ):
            assert await index_graph_nodes("ws-1", _GRAPH_DATA) == 0

    @pytest.mark.asyncio
    async def test_vector_count_mismatch_returns_zero(self) -> None:
        db = _mock_lancedb()
        with (
            patch(_GETDB, new_callable=AsyncMock, return_value=db),
            patch(_EMBED, new_callable=AsyncMock, return_value=[[0.1] * 10]),
        ):
            assert await index_graph_nodes("ws-1", _GRAPH_DATA) == 0

    @pytest.mark.asyncio
    async def test_nodes_without_id_are_filtered(self) -> None:
        db = _mock_lancedb()
        data = {
            "nodes": [{"id": "n.ok", "label": "Ok"}, {"label": "NoId"}],
            "edges": [],
        }
        with (
            patch(_GETDB, new_callable=AsyncMock, return_value=db),
            patch(
                _EMBED, new_callable=AsyncMock, return_value=[[0.1] * 10, [0.2] * 10]
            ),
        ):
            assert await index_graph_nodes("ws-1", data) == 1

    @pytest.mark.asyncio
    async def test_all_nodes_without_id_returns_zero(self) -> None:
        db = _mock_lancedb()
        data = {"nodes": [{"label": "A"}, {"label": "B"}], "edges": []}
        with (
            patch(_GETDB, new_callable=AsyncMock, return_value=db),
            patch(
                _EMBED, new_callable=AsyncMock, return_value=[[0.1] * 10, [0.2] * 10]
            ),
        ):
            assert await index_graph_nodes("ws-1", data) == 0

    @pytest.mark.asyncio
    async def test_creates_table_when_absent(self) -> None:
        db = _mock_lancedb()
        db.table_names = AsyncMock(return_value=[])
        with (
            patch(_GETDB, new_callable=AsyncMock, return_value=db),
            patch(_EMBED, new_callable=AsyncMock, return_value=_VEC3),
        ):
            await index_graph_nodes("ws-1", _GRAPH_DATA)
        db.create_table.assert_called_once()

    @pytest.mark.asyncio
    async def test_adds_to_existing_table(self) -> None:
        db = _mock_lancedb()
        table = AsyncMock()
        db.table_names = AsyncMock(return_value=["context_graph_nodes"])
        db.open_table = AsyncMock(return_value=table)
        with (
            patch(_GETDB, new_callable=AsyncMock, return_value=db),
            patch(_EMBED, new_callable=AsyncMock, return_value=_VEC3),
        ):
            await index_graph_nodes("ws-1", _GRAPH_DATA)
        table.add.assert_called_once()
        db.create_table.assert_not_called()

    @pytest.mark.asyncio
    async def test_db_exception_returns_zero(self) -> None:
        with (
            patch(_GETDB, side_effect=Exception("boom")),
            patch(_EMBED, new_callable=AsyncMock, return_value=_VEC3),
        ):
            assert await index_graph_nodes("ws-1", _GRAPH_DATA) == 0

    @pytest.mark.asyncio
    async def test_custom_collection_used(self) -> None:
        db = _mock_lancedb()
        db.table_names = AsyncMock(return_value=[])
        with (
            patch(_GETDB, new_callable=AsyncMock, return_value=db),
            patch(_EMBED, new_callable=AsyncMock, return_value=_VEC3),
        ):
            await index_graph_nodes("ws-1", _GRAPH_DATA, collection="custom_col")
        assert db.create_table.call_args.args[0] == "custom_col"

    @pytest.mark.asyncio
    async def test_metadata_contains_workspace_and_source(self) -> None:
        db = _mock_lancedb()
        db.table_names = AsyncMock(return_value=[])
        with (
            patch(_GETDB, new_callable=AsyncMock, return_value=db),
            patch(_EMBED, new_callable=AsyncMock, return_value=_VEC3),
        ):
            await index_graph_nodes("ws-9", _GRAPH_DATA)
        rows = db.create_table.call_args.kwargs["data"]
        meta = json.loads(rows[0]["metadata"])
        assert meta["workspace_id"] == "ws-9"
        assert meta["source_file"] == "auth.py"
        assert meta["file_type"] == "code"


# ---------------------------------------------------------------------------
# search_graph_nodes
# ---------------------------------------------------------------------------


class TestSearchGraphNodes:
    @pytest.mark.asyncio
    async def test_returns_node_ids_from_search(self) -> None:
        db = _mock_lancedb()
        _search_table(
            db,
            _df(
                [
                    (
                        0,
                        {
                            "id": "n.auth",
                            "metadata": '{"node_id":"n.auth","workspace_id":"ws-1"}',
                        },
                    )
                ]
            ),
        )
        with (
            patch(_GETDB, new_callable=AsyncMock, return_value=db),
            patch(_EMBED, new_callable=AsyncMock, return_value=[[0.1] * 10]),
        ):
            ids = await search_graph_nodes("authentication", "ws-1", top_k=5)
        assert ids == ["n.auth"]

    @pytest.mark.asyncio
    async def test_embeddings_empty_returns_empty(self) -> None:
        with patch(_EMBED, new_callable=AsyncMock, return_value=[]):
            assert await search_graph_nodes("q", "ws-1") == []

    @pytest.mark.asyncio
    async def test_collection_absent_returns_empty(self) -> None:
        db = _mock_lancedb()
        db.table_names = AsyncMock(return_value=[])
        with (
            patch(_GETDB, new_callable=AsyncMock, return_value=db),
            patch(_EMBED, new_callable=AsyncMock, return_value=[[0.1] * 10]),
        ):
            assert await search_graph_nodes("q", "ws-1") == []

    @pytest.mark.asyncio
    async def test_filters_by_workspace_id(self) -> None:
        db = _mock_lancedb()
        _search_table(
            db,
            _df(
                [
                    (
                        0,
                        {
                            "id": "n.a",
                            "metadata": '{"node_id":"n.a","workspace_id":"ws-1"}',
                        },
                    ),
                    (
                        1,
                        {
                            "id": "n.b",
                            "metadata": '{"node_id":"n.b","workspace_id":"OTHER"}',
                        },
                    ),
                ]
            ),
        )
        with (
            patch(_GETDB, new_callable=AsyncMock, return_value=db),
            patch(_EMBED, new_callable=AsyncMock, return_value=[[0.1] * 10]),
        ):
            ids = await search_graph_nodes("q", "ws-1", top_k=10)
        assert ids == ["n.a"]

    @pytest.mark.asyncio
    async def test_respects_top_k(self) -> None:
        db = _mock_lancedb()
        rows = [
            (
                i,
                {
                    "id": f"n.{i}",
                    "metadata": f'{{"node_id":"n.{i}","workspace_id":"ws-1"}}',
                },
            )
            for i in range(10)
        ]
        _search_table(db, _df(rows))
        with (
            patch(_GETDB, new_callable=AsyncMock, return_value=db),
            patch(_EMBED, new_callable=AsyncMock, return_value=[[0.1] * 10]),
        ):
            ids = await search_graph_nodes("q", "ws-1", top_k=3)
        assert len(ids) == 3

    @pytest.mark.asyncio
    async def test_malformed_metadata_skipped(self) -> None:
        db = _mock_lancedb()
        _search_table(db, _df([(0, {"id": "n.a", "metadata": "NOTJSON{"})]))
        with (
            patch(_GETDB, new_callable=AsyncMock, return_value=db),
            patch(_EMBED, new_callable=AsyncMock, return_value=[[0.1] * 10]),
        ):
            assert await search_graph_nodes("q", "ws-1") == []

    @pytest.mark.asyncio
    async def test_node_id_falls_back_to_row_id(self) -> None:
        db = _mock_lancedb()
        _search_table(
            db, _df([(0, {"id": "row-id", "metadata": '{"workspace_id":"ws-1"}'})])
        )
        with (
            patch(_GETDB, new_callable=AsyncMock, return_value=db),
            patch(_EMBED, new_callable=AsyncMock, return_value=[[0.1] * 10]),
        ):
            ids = await search_graph_nodes("q", "ws-1")
        assert ids == ["row-id"]

    @pytest.mark.asyncio
    async def test_lancedb_unavailable_returns_empty(self) -> None:
        with (
            patch(_GETDB, side_effect=Exception("LanceDB not available")),
            patch(_EMBED, new_callable=AsyncMock, return_value=[[0.1] * 10]),
        ):
            assert await search_graph_nodes("anything", "ws-1") == []


# ---------------------------------------------------------------------------
# purge_graph_index
# ---------------------------------------------------------------------------


class TestPurgeGraphIndex:
    @pytest.mark.asyncio
    async def test_purge_without_table_does_not_break(self) -> None:
        db = _mock_lancedb()
        db.table_names = AsyncMock(return_value=[])
        with patch(_GETDB, new_callable=AsyncMock, return_value=db):
            await purge_graph_index("ws-1")
        db.open_table.assert_not_called()

    @pytest.mark.asyncio
    async def test_purge_calls_delete_on_existing_table(self) -> None:
        db = _mock_lancedb()
        table = AsyncMock()
        db.table_names = AsyncMock(return_value=["context_graph_nodes"])
        db.open_table = AsyncMock(return_value=table)
        with patch(_GETDB, new_callable=AsyncMock, return_value=db):
            await purge_graph_index("ws-1")
        table.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_purge_delete_filter_contains_workspace(self) -> None:
        db = _mock_lancedb()
        table = AsyncMock()
        db.table_names = AsyncMock(return_value=["context_graph_nodes"])
        db.open_table = AsyncMock(return_value=table)
        with patch(_GETDB, new_callable=AsyncMock, return_value=db):
            await purge_graph_index("ws-xyz")
        assert "ws-xyz" in table.delete.call_args.args[0]

    @pytest.mark.asyncio
    async def test_purge_exception_does_not_raise(self) -> None:
        with patch(_GETDB, side_effect=Exception("boom")):
            await purge_graph_index("ws-1")

    @pytest.mark.asyncio
    async def test_purge_custom_collection_absent_is_noop(self) -> None:
        db = _mock_lancedb()
        db.table_names = AsyncMock(return_value=["context_graph_nodes"])
        with patch(_GETDB, new_callable=AsyncMock, return_value=db):
            await purge_graph_index("ws-1", collection="other")
        db.open_table.assert_not_called()


class TestPurgeGraphIndexRealTable:
    """Regressão ao vivo: `purge_graph_index()` usava `json_extract()` num
    predicado incompatível com o schema real da coluna `metadata` (Utf8,
    não LargeBinary) — o erro sempre acontecia e era engolido pelo
    `except Exception`, então a purga nunca removia nada de verdade. Os
    testes mockados existentes acima não pegam isso porque nunca rodam
    contra uma tabela LanceDB real.

    `lancedb.connect_async()` trava de forma determinística em CI (Linux)
    — o runtime Tokio nativo do LanceDB nunca entrega o resultado, dump de
    thread mostra tudo ocioso, sem depender de versão da lib. `fork()`
    (pytest-forked) NÃO isola isso — as threads de background do LanceDB
    (jemalloc, Tokio) não sobrevivem a um fork clássico de forma segura e
    o processo filho segfaulta em vez de travar. Isolamento de verdade
    acontece em step separado do workflow de CI (subprocesso real do SO —
    ver .github/workflows/vectora.yml); o marcador aqui só serve pra
    excluir esses testes da suíte principal via '-m "not real_lancedb"'.
    """

    pytestmark = pytest.mark.real_lancedb

    @pytest.mark.asyncio
    async def test_purge_remove_apenas_nos_do_workspace_em_tabela_real(
        self, tmp_path
    ) -> None:
        import lancedb

        db = await lancedb.connect_async(str(tmp_path))
        rows = [
            {
                "id": "n1",
                "vector": [0.1, 0.2],
                "text": "a",
                "metadata": json.dumps({"node_id": "n1", "workspace_id": "ws-1"}),
            },
            {
                "id": "n2",
                "vector": [0.3, 0.4],
                "text": "b",
                "metadata": json.dumps({"node_id": "n2", "workspace_id": "ws-1"}),
            },
            {
                "id": "n3",
                "vector": [0.5, 0.6],
                "text": "c",
                "metadata": json.dumps({"node_id": "n3", "workspace_id": "ws-2"}),
            },
        ]
        await db.create_table("context_graph_nodes", data=rows)

        with patch(_GETDB, new_callable=AsyncMock, return_value=db):
            await purge_graph_index("ws-1")

        table = await db.open_table("context_graph_nodes")
        remaining_ids = {row["id"] for row in await table.query().to_list()}
        assert remaining_ids == {"n3"}

    @pytest.mark.asyncio
    async def test_purge_nao_casa_workspace_que_e_prefixo_de_outro(
        self, tmp_path
    ) -> None:
        """Borda: "ws-1" não pode casar com "ws-10" por substring parcial —
        o filtro precisa delimitar o valor exato (aspas de fechamento do
        JSON), não só o prefixo."""
        import lancedb

        db = await lancedb.connect_async(str(tmp_path))
        rows = [
            {
                "id": "n1",
                "vector": [0.1, 0.2],
                "text": "a",
                "metadata": json.dumps({"node_id": "n1", "workspace_id": "ws-1"}),
            },
            {
                "id": "n2",
                "vector": [0.3, 0.4],
                "text": "b",
                "metadata": json.dumps({"node_id": "n2", "workspace_id": "ws-10"}),
            },
        ]
        await db.create_table("context_graph_nodes", data=rows)

        with patch(_GETDB, new_callable=AsyncMock, return_value=db):
            await purge_graph_index("ws-1")

        table = await db.open_table("context_graph_nodes")
        remaining_ids = {row["id"] for row in await table.query().to_list()}
        assert remaining_ids == {"n2"}
