"""Testes para backend/storage/lancedb/index.py.

Cobertura era zero antes destes testes. Foi assim que dois bugs reais
passaram despercebidos: `create_fts_index` chamava
`table.create_fts_index(...)`, método que não existe em `AsyncTable` (só
existe `create_index(column, config=FTS(...))`) — o `try/except` engolia o
`AttributeError` resultante como um warning genérico "falha ao criar FTS",
então nenhum índice FTS jamais foi criado por este caminho (o único
caminho que de fato funciona é o inline em `lancedb_backend.py`, agora
consolidado para usar este helper corrigido).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from lancedb.index import IvfPq

from backend.storage.lancedb.index import create_fts_index, create_ivf_index

from ._lancedb_fakes import fake_async_table


def _fake_table(row_count: int = 0):
    """Autospec real de `AsyncTable` — chamar um método/kwarg que a API
    real não aceita levanta `AttributeError`/`TypeError` de verdade, não
    passa silenciosamente como um `AsyncMock()` genérico faria."""
    table = fake_async_table()
    table.count_rows = AsyncMock(return_value=row_count)
    return table


class TestCreateIvfIndex:
    async def test_cria_indice_quando_atinge_min_rows(self):
        table = _fake_table(row_count=15_000)

        created = await create_ivf_index(table, min_rows=10_000)

        assert created is True
        table.create_index.assert_awaited_once()
        assert table.create_index.await_args is not None
        _, kwargs = table.create_index.await_args
        assert isinstance(kwargs["config"], IvfPq)

    async def test_abaixo_do_minimo_pula_sem_erro(self):
        table = _fake_table(row_count=100)

        created = await create_ivf_index(table, min_rows=10_000)

        assert created is False
        table.create_index.assert_not_awaited()

    async def test_indice_ja_existente_nao_propaga(self):
        table = _fake_table(row_count=20_000)
        table.create_index = AsyncMock(side_effect=RuntimeError("index already exists"))

        created = await create_ivf_index(table, min_rows=10_000)

        assert created is False

    async def test_count_rows_falhando_retorna_false_sem_lancar(self):
        table = _fake_table()
        table.count_rows = AsyncMock(side_effect=RuntimeError("storage indisponível"))

        created = await create_ivf_index(table, min_rows=10_000)

        assert created is False


class TestCreateFtsIndex:
    async def test_cria_via_create_index_com_config_fts(self):
        table = _fake_table()

        created = await create_fts_index(table, "text")

        assert created is True
        table.create_index.assert_awaited_once()
        assert table.create_index.await_args is not None
        args, kwargs = table.create_index.await_args
        assert args[0] == "text"
        assert kwargs["replace"] is False
        assert kwargs["config"].language == "English"
        # Regressão real: o método antigo (table.create_fts_index) não
        # existe em AsyncTable — este teste garante que o caminho certo
        # (table.create_index com config=FTS(...)) é o que é chamado.
        assert not hasattr(table, "create_fts_index")

    async def test_idioma_customizado_repassado_pro_fts_config(self):
        table = _fake_table()

        await create_fts_index(table, "text", language="Portuguese")

        assert table.create_index.await_args is not None
        _, kwargs = table.create_index.await_args
        assert kwargs["config"].language == "Portuguese"

    async def test_indice_ja_existente_nao_propaga(self):
        table = _fake_table()
        table.create_index = AsyncMock(side_effect=RuntimeError("FTS already exists"))

        created = await create_fts_index(table, "text")

        assert created is False

    async def test_falha_generica_nao_propaga(self):
        table = _fake_table()
        table.create_index = AsyncMock(side_effect=RuntimeError("storage indisponível"))

        created = await create_fts_index(table, "text")

        assert created is False


class TestCreateIvfIndexRealTable:
    """Regressão ao vivo: `create_index(index_type=..., num_partitions=...,
    num_sub_vectors=...)` não existe na API real — precisa de
    `config=IvfPq(...)`. O autospec acima já pega a divergência de
    assinatura, mas só uma tabela real confirma que o índice é
    efetivamente criado (sem erro do lado do datafusion/lance).

    `lancedb.connect_async()` real trava de forma determinística em CI
    (Linux) — ver comentário completo em pyproject.toml (seção `timeout`).
    Isolado em step separado do workflow via marcador `real_lancedb`."""

    pytestmark = pytest.mark.real_lancedb

    async def test_cria_indice_ivf_pq_de_verdade_em_tabela_real(self, tmp_path) -> None:
        import lancedb
        import pyarrow as pa

        db = await lancedb.connect_async(str(tmp_path))
        schema = pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("vector", pa.list_(pa.float32(), 8)),
            ]
        )
        table = await db.create_table("vecs", schema=schema)
        await table.add(
            [{"id": str(i), "vector": [float(i % 7)] * 8} for i in range(300)]
        )

        created = await create_ivf_index(
            table, min_rows=100, num_partitions=4, num_sub_vectors=2
        )

        assert created is True
