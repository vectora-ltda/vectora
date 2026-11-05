"""Tests — storage/lancedb/ e get_vector_store.

Modo "lite" usa diretório temporário; modo "complete" (Qdrant) é skippado
sem qdrant_url configurado.

Todo o arquivo usa lancedb.connect_async() real (direto ou via
get_lancedb()/get_vector_store()) — trava de forma determinística em CI
(Linux), ver comentário completo em pyproject.toml (seção `timeout`).
Isolado em step separado do workflow via marcador `real_lancedb`.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.real_lancedb


@pytest.fixture(autouse=True)
def reset_storage_singletons():
    import backend.storage.factory as _fac

    _fac._reset_singletons()
    yield
    _fac._reset_singletons()


class TestLanceDBConnection:
    """Cache de conexão LanceDB."""

    @pytest.mark.asyncio
    async def test_connection_returns_db(self, tmp_path):
        from backend.storage.lancedb.connection import get_lancedb

        db = await get_lancedb(str(tmp_path))
        assert db is not None

    @pytest.mark.asyncio
    async def test_connection_singleton(self, tmp_path):
        from backend.storage.lancedb.connection import get_lancedb

        db1 = await get_lancedb(str(tmp_path))
        db2 = await get_lancedb(str(tmp_path))
        assert db1 is db2

    @pytest.mark.asyncio
    async def test_close_all_fecha_conexoes_de_verdade_e_esvazia_cache(self, tmp_path):
        """`close_all()` chama `AsyncConnection.close()` de verdade, não só
        limpa o dict Python — confirmado pelo fato de que uma conexão
        fechada rejeita operações subsequentes."""
        from backend.storage.lancedb.connection import LanceDBConnectionCache

        cache = LanceDBConnectionCache()
        db = await cache.connect(str(tmp_path))
        assert cache.cached_paths == [tmp_path]

        await cache.close_all()

        assert cache.cached_paths == []
        with pytest.raises(RuntimeError, match=r"[Cc]onnection is closed"):
            await db.table_names()

    @pytest.mark.asyncio
    async def test_close_all_com_conexao_ja_fechada_nao_propaga(self, tmp_path):
        """Borda: se uma conexão já foi fechada por outro caminho, `close()`
        chamado de novo não pode derrubar o shutdown das outras."""
        from backend.storage.lancedb.connection import LanceDBConnectionCache

        cache = LanceDBConnectionCache()
        db = await cache.connect(str(tmp_path))
        db.close()  # fecha "por fora" antes do close_all() rodar

        await cache.close_all()  # não deve lançar

        assert cache.cached_paths == []


class TestGetVectorStore:
    """get_vector_store abre AsyncTable existente ou retorna None."""

    @pytest.mark.asyncio
    async def test_returns_none_for_nonexistent(self, tmp_path):
        """Tabela inexistente retorna None — comportamento documentado."""
        from backend.storage.factory import get_vector_store

        tbl = await get_vector_store("articles", path=str(tmp_path / "lancedb"))
        assert tbl is None

    @pytest.mark.asyncio
    async def test_returns_table_when_exists(self, tmp_path):
        """Tabela existente é retornada como AsyncTable."""
        import lancedb
        import pyarrow as pa

        from backend.storage.factory import get_vector_store

        lancedb_path = str(tmp_path / "lancedb")

        # Cria a tabela primeiro via lancedb diretamente
        db = await lancedb.connect_async(lancedb_path)
        schema = pa.schema([pa.field("id", pa.string()), pa.field("text", pa.string())])
        await db.create_table("articles", schema=schema)

        tbl = await get_vector_store("articles", path=lancedb_path)
        assert tbl is not None

    @pytest.mark.asyncio
    async def test_get_lancedb_connection_caches(self, tmp_path):
        """Duas chamadas com mesmo path retornam mesma conexão LanceDB."""
        from backend.storage.lancedb.connection import get_lancedb

        db1 = await get_lancedb(str(tmp_path / "db1"))
        db2 = await get_lancedb(str(tmp_path / "db1"))
        assert db1 is db2

    @pytest.mark.asyncio
    async def test_tabela_existente_agenda_otimizacao_periodica_uma_vez(self, tmp_path):
        """`optimize_table`/`schedule_optimize` existiam mas nunca eram
        chamados em lugar nenhum do app — nenhuma tabela LanceDB recebia
        compactação periódica. O primeiro `get_vector_store` bem-sucedido
        de uma coleção agenda a task; chamadas seguintes reusam a mesma
        (não agenda duas vezes)."""
        import lancedb
        import pyarrow as pa

        import backend.storage.factory as _fac
        from backend.storage.factory import get_vector_store

        lancedb_path = str(tmp_path / "lancedb")
        db = await lancedb.connect_async(lancedb_path)
        schema = pa.schema([pa.field("id", pa.string()), pa.field("text", pa.string())])
        await db.create_table("articles", schema=schema)

        await get_vector_store("articles", path=lancedb_path)
        cache_key = f"{lancedb_path}::articles"
        assert cache_key in _fac._optimize_tasks
        task = _fac._optimize_tasks[cache_key]
        assert not task.done()

        await get_vector_store("articles", path=lancedb_path)
        assert _fac._optimize_tasks[cache_key] is task

    @pytest.mark.asyncio
    async def test_tabela_inexistente_nao_agenda_otimizacao(self, tmp_path):
        """Erro/borda: `table is None` (coleção ainda não criada) não deve
        tentar agendar otimização de uma tabela que não existe."""
        import backend.storage.factory as _fac
        from backend.storage.factory import get_vector_store

        lancedb_path = str(tmp_path / "lancedb")
        tbl = await get_vector_store("nao-existe", path=lancedb_path)

        assert tbl is None
        assert _fac._optimize_tasks == {}
