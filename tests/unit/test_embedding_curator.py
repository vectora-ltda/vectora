"""Testes de `backend/embedding/curator.py::_sample_recent_docs`.

Regressão ao vivo: `table.to_pandas()` é async (`lancedb.connect_async` devolve
um `AsyncTable`) — o código envolvia a chamada em `asyncio.to_thread(lambda:
t.to_pandas().head(5))`, que roda a lambda numa thread sem event loop. Ali
dentro, `t.to_pandas()` só cria o coroutine (nunca executa) e `.head(5)`
falha com `AttributeError` — engolido pelo `except Exception` externo, então
toda coleção sempre devolvia zero amostras, e o coroutine nunca-aguardado
disparava `RuntimeWarning: coroutine 'AsyncTable.to_pandas' was never
awaited` no log a cada chamada.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from backend.embedding.curator import _sample_recent_docs


@pytest.fixture
def mock_db():
    db = AsyncMock()
    with patch(
        "lancedb.connect_async",
        AsyncMock(return_value=db),
    ):
        yield db


@pytest.fixture
def mock_settings():
    with patch("backend.settings.settings.lancedb_dir", "/tmp/fake-lancedb"):
        yield


class TestSampleRecentDocs:
    @pytest.mark.asyncio
    async def test_amostra_documentos_do_workspace_certo(self, mock_db, mock_settings):
        mock_db.list_tables = AsyncMock(return_value=MagicMock(tables=["articles"]))
        table = AsyncMock()
        df = pd.DataFrame(
            [
                {
                    "text": "conteúdo do workspace certo",
                    "metadata": '{"workspace_id": "ws-1", "source": "doc.md"}',
                }
            ]
        )
        table.to_pandas = AsyncMock(return_value=df)
        mock_db.open_table = AsyncMock(return_value=table)

        samples = await _sample_recent_docs("ws-1")

        assert len(samples) == 1
        assert samples[0]["collection"] == "articles"
        assert samples[0]["source"] == "doc.md"
        assert "conteúdo do workspace certo" in samples[0]["text"]

    @pytest.mark.asyncio
    async def test_filtra_documentos_de_outro_workspace(self, mock_db, mock_settings):
        mock_db.list_tables = AsyncMock(return_value=MagicMock(tables=["articles"]))
        table = AsyncMock()
        df = pd.DataFrame(
            [{"text": "de outro workspace", "metadata": '{"workspace_id": "outro"}'}]
        )
        table.to_pandas = AsyncMock(return_value=df)
        mock_db.open_table = AsyncMock(return_value=table)

        samples = await _sample_recent_docs("ws-1")

        assert samples == []

    @pytest.mark.asyncio
    async def test_colecao_sem_tabela_nao_derruba_a_amostragem(
        self, mock_db, mock_settings
    ):
        mock_db.list_tables = AsyncMock(return_value=MagicMock(tables=["quebrada"]))
        mock_db.open_table = AsyncMock(side_effect=RuntimeError("tabela sumiu"))

        samples = await _sample_recent_docs("ws-1")

        assert samples == []
