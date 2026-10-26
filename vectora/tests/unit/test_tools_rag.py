"""Tests for src/tools/rag.py"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.tools.context import ToolContext


class TestVectorSearch:
    @pytest.mark.asyncio
    async def test_vector_search_full_path_no_rerank(self):
        """vector_search com VectorStoreBackend mockado (LanceDB ou Qdrant,
        indiferente pro tool — ele só fala com o Protocol) + Cohere embeddings."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from backend.storage.vectorstore.base import VectorHit

        mock_backend = AsyncMock()
        mock_backend.search = AsyncMock(
            return_value=[
                VectorHit(
                    id="1",
                    score=0.1,
                    content="content",
                    metadata={},
                    collection="articles",
                )
            ]
        )

        mock_embeddings = MagicMock()
        mock_embeddings.return_value.embed_query.return_value = [0.1, 0.2, 0.3]

        from backend.tools.rag import vector_search

        with patch("backend.tools.rag.settings") as ms:
            ms.get_cohere_api_key.return_value = "test-key"
            ms.embedding_model = "embed-english-v3.0"
            ms.reranker_type = "none"
            with (
                patch(
                    "backend.storage.factory._build_lc_embeddings",
                    mock_embeddings,
                ),
                patch(
                    "backend.storage.factory.get_vector_store_backend",
                    AsyncMock(return_value=mock_backend),
                ),
            ):
                result = await vector_search(
                    ctx=ToolContext(),
                    query="test query",
                    collection="articles",
                    limit=5,
                )
        data = json.loads(result)
        assert "results" in data
        assert len(data["results"]) == 1
        assert data["results"][0]["provenance"]["source"] == "rag"
        assert data["retrieval"]["backend"] == "AsyncMock"

    @pytest.mark.asyncio
    async def test_vector_search_table_not_found(self):
        """Coleção ausente: backend.search() devolve [] (contrato do
        Protocol), vector_search reporta no_results."""
        from unittest.mock import AsyncMock, MagicMock, patch

        mock_backend = AsyncMock()
        mock_backend.search = AsyncMock(return_value=[])

        mock_embeddings = MagicMock()
        mock_embeddings.return_value.embed_query.return_value = [0.1, 0.2]

        from backend.tools.rag import vector_search

        with patch("backend.tools.rag.settings") as ms:
            ms.get_cohere_api_key.return_value = "test-key"
            ms.embedding_model = "embed-english-v3.0"
            with (
                patch(
                    "backend.storage.factory._build_lc_embeddings",
                    mock_embeddings,
                ),
                patch(
                    "backend.storage.factory.get_vector_store_backend",
                    AsyncMock(return_value=mock_backend),
                ),
            ):
                result = await vector_search(
                    ctx=ToolContext(), query="test", collection="articles", limit=5
                )
        data = json.loads(result)
        assert data["status"] == "no_results"

    @pytest.mark.asyncio
    async def test_missing_dependencies_returns_message(self):
        """Verifica o comportamento de missing dependencies returns message."""
        from backend.tools.rag import vector_search

        with patch("backend.tools.rag.settings") as mock_settings:
            with patch("backend.tools.rag.lancedb", None):
                result = await vector_search(
                    ctx=ToolContext(), query="test", collection="articles", limit=5
                )
        assert "missing" in result.lower() or isinstance(result, str)

    @pytest.mark.asyncio
    async def test_missing_api_key_returns_error(self):
        """Verifica o comportamento de missing api key returns error."""
        from backend.tools.rag import vector_search

        with patch("backend.tools.rag.settings") as mock_settings:
            mock_settings.get_cohere_api_key.return_value = None
            with (
                patch("backend.tools.rag.lancedb", MagicMock()),
                patch("backend.storage.factory._build_lc_embeddings", lambda: None),
            ):
                result = await vector_search(
                    ctx=ToolContext(), query="test", collection="articles", limit=5
                )
        data = json.loads(result)
        assert data.get("status") in ("failed", "error")

    @pytest.mark.asyncio
    async def test_rerank_preserva_id_colecao_e_score_de_retrieval(self):
        """Verifica o comportamento de rerank preserva id colecao e score de retrieval."""
        from backend.storage.vectorstore.base import VectorHit
        from backend.tools.rag import vector_search

        backend = AsyncMock()
        backend.search = AsyncMock(
            return_value=[
                VectorHit("first", 0.1, "one", {}, "articles"),
                VectorHit("second", 0.2, "two", {}, "docs"),
            ]
        )

        class _ReverseReranker:
            async def acompress_documents(self, documents, query):
                """Executa a operação acompress documents."""
                return list(reversed(documents))

        embeddings = MagicMock()
        embeddings.return_value.embed_query.return_value = [0.1]
        with patch("backend.tools.rag.settings") as ms:
            ms.get_cohere_api_key.return_value = "test-key"
            ms.embedding_model = "embed"
            ms.rag_hybrid_enabled = False
            with (
                patch("backend.tools.rag.lancedb", MagicMock()),
                patch("backend.storage.factory._build_lc_embeddings", embeddings),
                patch(
                    "backend.storage.factory.get_vector_store_backend",
                    AsyncMock(return_value=backend),
                ),
                patch(
                    "backend.tools.rag._build_reranker", return_value=_ReverseReranker()
                ),
            ):
                data = json.loads(
                    await vector_search(
                        ctx=ToolContext(), query="q", collection="articles", limit=2
                    )
                )

        assert [item["id"] for item in data["results"]] == ["second", "first"]
        assert data["results"][0]["collection"] == "docs"
        assert data["results"][0]["score"] == 0.2
        assert data["retrieval"]["reranker_status"] == "applied"

    @pytest.mark.asyncio
    async def test_no_results_emite_telemetria_com_contagem_zero(self):
        """Verifica o comportamento de no results emite telemetria com contagem zero."""
        from backend.tools.rag import vector_search

        backend = AsyncMock()
        backend.search = AsyncMock(return_value=[])
        embeddings = MagicMock()
        embeddings.return_value.embed_query.return_value = [0.1]
        telemetry_mock = AsyncMock()
        with patch("backend.tools.rag.settings") as ms:
            ms.get_cohere_api_key.return_value = "test-key"
            ms.embedding_model = "embed"
            ms.rag_hybrid_enabled = False
            with (
                patch("backend.tools.rag.lancedb", MagicMock()),
                patch("backend.storage.factory._build_lc_embeddings", embeddings),
                patch(
                    "backend.storage.factory.get_vector_store_backend",
                    AsyncMock(return_value=backend),
                ),
                patch("backend.persistence.telemetry.telemetry", telemetry_mock),
            ):
                data = json.loads(
                    await vector_search(
                        ctx=ToolContext(),
                        query="missing",
                        collection="articles",
                        limit=2,
                    )
                )

        assert data["status"] == "no_results"
        telemetry_mock.record_retrieval.assert_awaited_once_with(
            query="missing",
            collections=["articles"],
            result_count=0,
            retrieval_method="vector",
        )


class TestReciprocalRankFusion:
    """`_reciprocal_rank_fusion` funde ranking vetorial (distância) com
    ranking textual (relevância) usando só a ORDEM de cada lista — não os
    valores absolutos, que têm escalas incompatíveis entre os dois métodos."""

    def test_documento_presente_nos_dois_rankings_sobe_de_posicao(self):
        """Verifica o comportamento de documento presente nos dois rankings sobe de posicao."""
        from backend.tools.rag import _reciprocal_rank_fusion

        vector_results = [
            {"id": "a", "collection": "c", "score": 0.1, "content": "", "metadata": {}},
            {"id": "b", "collection": "c", "score": 0.2, "content": "", "metadata": {}},
        ]
        text_results = [
            {"id": "b", "collection": "c", "score": 5.0, "content": "", "metadata": {}},
        ]

        fused = _reciprocal_rank_fusion(vector_results, text_results, limit=5)

        # "b" está em 2º no vetorial mas em 1º no textual — soma dos dois
        # ranks recíprocos supera "a" (só presente no vetorial, em 1º).
        assert fused[0]["id"] == "b"

    def test_documento_presente_so_em_uma_lista_ainda_aparece(self):
        """Verifica o comportamento de documento presente so em uma lista ainda aparece."""
        from backend.tools.rag import _reciprocal_rank_fusion

        vector_results = [
            {"id": "a", "collection": "c", "score": 0.1, "content": "", "metadata": {}},
        ]
        text_results: list[dict] = []

        fused = _reciprocal_rank_fusion(vector_results, text_results, limit=5)

        assert [r["id"] for r in fused] == ["a"]

    def test_ambas_listas_vazias_retorna_lista_vazia(self):
        """Verifica o comportamento de ambas listas vazias retorna lista vazia."""
        from backend.tools.rag import _reciprocal_rank_fusion

        assert _reciprocal_rank_fusion([], [], limit=5) == []

    def test_respeita_o_limit(self):
        """Verifica o comportamento de respeita o limit."""
        from backend.tools.rag import _reciprocal_rank_fusion

        vector_results = [
            {
                "id": str(i),
                "collection": "c",
                "score": float(i),
                "content": "",
                "metadata": {},
            }
            for i in range(10)
        ]

        fused = _reciprocal_rank_fusion(vector_results, [], limit=3)

        assert len(fused) == 3


class TestRetrievalProvenance:
    def test_attach_preserva_resultado_e_registra_rank(self):
        """Verifica o comportamento de attach preserva resultado e registra rank."""
        from backend.tools.rag import _attach_retrieval_provenance

        result = _attach_retrieval_provenance(
            [{"id": "a", "collection": "docs", "score": 0.2}],
            backend_name="QdrantBackend",
            retrieval_method="hybrid_rrf",
        )

        assert result[0]["id"] == "a"
        assert result[0]["provenance"] == {
            "source": "rag",
            "backend": "QdrantBackend",
            "collection": "docs",
            "method": "hybrid_rrf",
            "rank": 1,
            "score": 0.2,
        }

    def test_attach_preserva_identidade_depois_de_reordenar(self) -> None:
        """Verifica o comportamento de attach preserva identidade depois de reordenar."""
        from backend.tools.rag import _attach_retrieval_provenance

        result = _attach_retrieval_provenance(
            [
                {
                    "id": "b",
                    "collection": "qdrant_docs",
                    "score": 0.8,
                    "content": "b",
                },
                {
                    "id": "a",
                    "collection": "articles",
                    "score": 0.2,
                    "content": "a",
                },
            ],
            backend_name="QdrantBackend",
            retrieval_method="hybrid_rrf",
        )
        assert [(item["id"], item["provenance"]["collection"]) for item in result] == [
            ("b", "qdrant_docs"),
            ("a", "articles"),
        ]


class TestVectorSearchHybrid:
    """`vector_search` funde busca vetorial com busca textual quando
    `settings.rag_hybrid_enabled` (default True) — usa `search_text` do
    backend além de `search`."""

    @pytest.mark.asyncio
    async def test_hybrid_enabled_chama_search_text_e_funde_resultados(self):
        """Verifica o comportamento de hybrid enabled chama search text e funde resultados."""
        from backend.storage.vectorstore.base import VectorHit
        from backend.tools.rag import vector_search

        mock_backend = AsyncMock()
        mock_backend.search = AsyncMock(
            return_value=[
                VectorHit(
                    id="vec-only",
                    score=0.1,
                    content="c1",
                    metadata={},
                    collection="articles",
                )
            ]
        )
        mock_backend.search_text = AsyncMock(
            return_value=[
                VectorHit(
                    id="text-only",
                    score=9.0,
                    content="c2",
                    metadata={},
                    collection="articles",
                )
            ]
        )
        mock_embeddings = MagicMock()
        mock_embeddings.return_value.embed_query.return_value = [0.1, 0.2, 0.3]

        with patch("backend.tools.rag.settings") as ms:
            ms.get_cohere_api_key.return_value = "test-key"
            ms.embedding_model = "embed-english-v3.0"
            ms.reranker_type = "none"
            ms.rag_hybrid_enabled = True
            ms.rag_hybrid_fetch_limit = 20
            with (
                patch("backend.storage.factory._build_lc_embeddings", mock_embeddings),
                patch(
                    "backend.storage.factory.get_vector_store_backend",
                    AsyncMock(return_value=mock_backend),
                ),
            ):
                result = await vector_search(
                    ctx=ToolContext(),
                    query="test query",
                    collection="articles",
                    limit=5,
                )

        mock_backend.search_text.assert_awaited_once()
        data = json.loads(result)
        ids = {r["content"] for r in data["results"]}
        assert "c1" in ids
        assert "c2" in ids

    @pytest.mark.asyncio
    async def test_hybrid_disabled_nao_chama_search_text(self):
        """Verifica o comportamento de hybrid disabled nao chama search text."""
        from backend.storage.vectorstore.base import VectorHit
        from backend.tools.rag import vector_search

        mock_backend = AsyncMock()
        mock_backend.search = AsyncMock(
            return_value=[
                VectorHit(
                    id="vec-only",
                    score=0.1,
                    content="c1",
                    metadata={},
                    collection="articles",
                )
            ]
        )
        mock_backend.search_text = AsyncMock(return_value=[])
        mock_embeddings = MagicMock()
        mock_embeddings.return_value.embed_query.return_value = [0.1, 0.2, 0.3]

        with patch("backend.tools.rag.settings") as ms:
            ms.get_cohere_api_key.return_value = "test-key"
            ms.embedding_model = "embed-english-v3.0"
            ms.reranker_type = "none"
            ms.rag_hybrid_enabled = False
            with (
                patch("backend.storage.factory._build_lc_embeddings", mock_embeddings),
                patch(
                    "backend.storage.factory.get_vector_store_backend",
                    AsyncMock(return_value=mock_backend),
                ),
            ):
                result = await vector_search(
                    ctx=ToolContext(),
                    query="test query",
                    collection="articles",
                    limit=5,
                )

        mock_backend.search_text.assert_not_called()
        data = json.loads(result)
        assert len(data["results"]) == 1


class TestVectorSearchBucketFanout:
    """Sem `collection` explícito, a busca varre só os buckets ativos do
    workspace (backend/services/rag_buckets.py) — não a tabela `articles`
    inteira sempre."""

    def _mock_backend(self, tables: dict[str, list[dict]]):
        """`tables` mapeia nome de coleção -> lista de linhas (dicts com
        id/_distance/text/metadata) que essa coleção "contém". Devolve um
        `VectorStoreBackend` fake cujo `.search()` só sabe responder pelas
        coleções presentes em `tables` — mesmo contrato que
        LanceDBBackend/QdrantBackend implementam de verdade."""
        from backend.storage.vectorstore.base import VectorHit

        async def _search(collection: str, query_vector, limit: int):
            """Executa a operação  search."""
            if collection not in tables:
                return []
            return [
                VectorHit(
                    id=str(row["id"]),
                    score=float(row.get("_distance", 0.0)),
                    content=row["text"],
                    metadata=json.loads(row.get("metadata", "{}")),
                    collection=collection,
                )
                for row in tables[collection]
            ]

        mock_backend = AsyncMock()
        mock_backend.search = AsyncMock(side_effect=_search)
        return mock_backend

    @pytest.mark.asyncio
    async def test_searches_only_active_buckets_of_workspace(self, tmp_path):
        """Verifica o comportamento de searches only active buckets of workspace."""
        from backend.services import rag_buckets
        from backend.tools.rag import vector_search
        from backend.workspace.runtime_settings import RuntimeSettings

        rs = RuntimeSettings(path=tmp_path / "checkpoints.db")
        active = rag_buckets.create_bucket(rs, workspace_id="ws1", name="Godot")
        inactive = rag_buckets.create_bucket(rs, workspace_id="ws1", name="TS libs")
        rag_buckets.set_active(rs, workspace_id="ws1", bucket_id=active.id, active=True)
        # inactive nunca é ativado — não deve aparecer na busca.

        tables = {
            f"bucket_{active.id}": [
                {"id": "1", "_distance": 0.1, "text": "godot doc", "metadata": "{}"}
            ],
            f"bucket_{inactive.id}": [
                {"id": "2", "_distance": 0.05, "text": "ts lib doc", "metadata": "{}"}
            ],
        }
        mock_backend = self._mock_backend(tables)
        mock_embeddings = MagicMock()
        mock_embeddings.return_value.embed_query.return_value = [0.1, 0.2, 0.3]

        with (
            patch("backend.tools.rag.settings") as ms,
            patch("backend.workspace.runtime_settings.runtime_settings", rs),
        ):
            ms.get_cohere_api_key.return_value = "test-key"
            ms.embedding_model = "embed-english-v3.0"
            with (
                patch(
                    "backend.storage.factory._build_lc_embeddings",
                    mock_embeddings,
                ),
                patch(
                    "backend.storage.factory.get_vector_store_backend",
                    AsyncMock(return_value=mock_backend),
                ),
            ):
                result = await vector_search(
                    ctx=ToolContext(workspace_id="ws1"), query="test", limit=5
                )

        data = json.loads(result)
        contents = [r["content"] for r in data["results"]]
        assert contents == ["godot doc"]

    @pytest.mark.asyncio
    async def test_no_active_buckets_falls_back_to_legacy_articles_table(
        self, tmp_path
    ):
        """Verifica o comportamento de no active buckets falls back to legacy articles table."""
        from backend.tools.rag import vector_search
        from backend.workspace.runtime_settings import RuntimeSettings

        rs = RuntimeSettings(path=tmp_path / "checkpoints.db")
        tables = {
            "articles": [
                {"id": "1", "_distance": 0.1, "text": "dado legado", "metadata": "{}"}
            ]
        }
        mock_backend = self._mock_backend(tables)
        mock_embeddings = MagicMock()
        mock_embeddings.return_value.embed_query.return_value = [0.1, 0.2, 0.3]

        with (
            patch("backend.tools.rag.settings") as ms,
            patch("backend.workspace.runtime_settings.runtime_settings", rs),
        ):
            ms.get_cohere_api_key.return_value = "test-key"
            ms.embedding_model = "embed-english-v3.0"
            with (
                patch(
                    "backend.storage.factory._build_lc_embeddings",
                    mock_embeddings,
                ),
                patch(
                    "backend.storage.factory.get_vector_store_backend",
                    AsyncMock(return_value=mock_backend),
                ),
            ):
                result = await vector_search(
                    ctx=ToolContext(workspace_id="ws-sem-buckets"),
                    query="test",
                    limit=5,
                )

        data = json.loads(result)
        assert [r["content"] for r in data["results"]] == ["dado legado"]

    @pytest.mark.asyncio
    async def test_explicit_collection_overrides_active_buckets(self, tmp_path):
        """Passar `collection` manualmente ignora os buckets ativos —
        comportamento avançado preservado (regressão)."""
        from backend.services import rag_buckets
        from backend.tools.rag import vector_search
        from backend.workspace.runtime_settings import RuntimeSettings

        rs = RuntimeSettings(path=tmp_path / "checkpoints.db")
        bucket = rag_buckets.create_bucket(rs, workspace_id="ws1", name="Godot")
        rag_buckets.set_active(rs, workspace_id="ws1", bucket_id=bucket.id, active=True)
        tables = {
            f"bucket_{bucket.id}": [
                {"id": "1", "_distance": 0.1, "text": "bucket doc", "metadata": "{}"}
            ],
            "custom_collection": [
                {"id": "2", "_distance": 0.1, "text": "manual doc", "metadata": "{}"}
            ],
        }
        mock_backend = self._mock_backend(tables)
        mock_embeddings = MagicMock()
        mock_embeddings.return_value.embed_query.return_value = [0.1, 0.2, 0.3]

        with (
            patch("backend.tools.rag.settings") as ms,
            patch("backend.workspace.runtime_settings.runtime_settings", rs),
        ):
            ms.get_cohere_api_key.return_value = "test-key"
            ms.embedding_model = "embed-english-v3.0"
            with (
                patch(
                    "backend.storage.factory._build_lc_embeddings",
                    mock_embeddings,
                ),
                patch(
                    "backend.storage.factory.get_vector_store_backend",
                    AsyncMock(return_value=mock_backend),
                ),
            ):
                result = await vector_search(
                    ctx=ToolContext(workspace_id="ws1"),
                    query="test",
                    collection="custom_collection",
                    limit=5,
                )

        data = json.loads(result)
        assert [r["content"] for r in data["results"]] == ["manual doc"]


class TestEmbedding:
    @pytest.mark.asyncio
    async def test_queue_not_enabled_returns_error(self):
        """Verifica o comportamento de queue not enabled returns error."""
        from backend.tools.rag import embedding

        with patch("backend.tools.rag.settings") as mock_settings:
            mock_settings.embedding_queue_enabled = False
            result = await embedding(text="doc", collection="articles")
        data = json.loads(result)
        assert data["status"] == "error"

    @pytest.mark.asyncio
    async def test_enqueue_success_returns_fire_and_forget(self):
        """Verifica o comportamento de enqueue success returns fire and forget."""
        from backend.tools.rag import embedding

        mock_queue = AsyncMock()
        mock_queue.enqueue.return_value = "queue-id-123"

        with patch("backend.tools.rag.settings") as mock_settings:
            mock_settings.embedding_queue_enabled = True
            mock_settings.embedding_queue_dsn = "sqlite:///test.db"
            with patch(
                "backend.tools.rag.get_embedding_queue",
                new_callable=AsyncMock,
                return_value=mock_queue,
            ):
                result = await embedding(text="sample text", collection="articles")
        data = json.loads(result)
        assert data["status"] == "fire_and_forget"
        assert data["queue_id"] == "queue-id-123"
        assert data["collection"] == "articles"

    @pytest.mark.asyncio
    async def test_enqueue_exception_returns_error(self):
        """Verifica o comportamento de enqueue exception returns error."""
        from backend.tools.rag import embedding

        with patch("backend.tools.rag.settings") as mock_settings:
            mock_settings.embedding_queue_enabled = True
            mock_settings.embedding_queue_dsn = "sqlite:///test.db"
            with patch(
                "backend.tools.rag.get_embedding_queue",
                new_callable=AsyncMock,
                side_effect=Exception("DB connection failed"),
            ):
                result = await embedding(text="sample text", collection="articles")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "DB connection failed" in data["error"]


class TestIngestDocs:
    @pytest.mark.asyncio
    async def test_unsafe_path_denied(self):
        """Verifica o comportamento de unsafe path denied."""
        from backend.tools.rag import ingest_docs

        with patch("backend.tools.rag.settings") as mock_settings:
            mock_settings.enable_file_operations = True
            with patch(
                "backend.tools.fs._confine",
                return_value=(None, "Access denied: outside allowed directory"),
            ):
                result = await ingest_docs(
                    ctx=ToolContext(), directory_path="/etc", collection="articles"
                )
        assert "denied" in result.lower() or "Access" in result

    @pytest.mark.asyncio
    async def test_not_a_directory_returns_error(self, tmp_path):
        """Verifica o comportamento de not a directory returns error."""
        from backend.tools.rag import ingest_docs

        not_dir = tmp_path / "file.txt"
        not_dir.write_text("I am a file")

        with patch("backend.tools.rag.settings") as mock_settings:
            mock_settings.enable_file_operations = True
            with patch("backend.tools.fs._confine", return_value=(not_dir, "")):
                result = await ingest_docs(
                    ctx=ToolContext(),
                    directory_path=str(not_dir),
                    collection="articles",
                )
        assert "Not a directory" in result

    @pytest.mark.asyncio
    async def test_no_files_found_returns_no_files(self, tmp_path):
        """Verifica o comportamento de no files found returns no files."""
        from backend.tools.rag import ingest_docs

        with patch("backend.tools.rag.settings") as mock_settings:
            mock_settings.enable_file_operations = True
            with patch("backend.tools.fs._confine", return_value=(tmp_path, "")):
                with patch(
                    "backend.services.ignore.load_ignore_spec", return_value=None
                ):
                    result = await ingest_docs(
                        ctx=ToolContext(),
                        directory_path=str(tmp_path),
                        collection="articles",
                        glob_pattern="**/*.md",
                    )
        data = json.loads(result)
        assert data["status"] == "no_files"

    @pytest.mark.asyncio
    async def test_chunk_fail_counts_as_failure(self, tmp_path):
        """Verifica o comportamento de chunk fail counts as failure."""
        from backend.tools.rag import ingest_docs

        md_file = tmp_path / "doc.md"
        md_file.write_text("# Content")

        with patch("backend.tools.rag.settings") as mock_settings:
            mock_settings.enable_file_operations = True
            with patch("backend.tools.fs._confine", return_value=(tmp_path, "")):
                with patch("backend.services.ignore.is_ignored", return_value=False):
                    with patch(
                        "backend.services.ignore.load_ignore_spec",
                        return_value=None,
                    ):
                        with patch(
                            "backend.tools.rag.embedding",
                            AsyncMock(side_effect=Exception("embedding fail")),
                        ):
                            result = await ingest_docs(
                                ctx=ToolContext(),
                                directory_path=str(tmp_path),
                                collection="articles",
                                glob_pattern="**/*.md",
                            )
        data = json.loads(result)
        assert data["status"] == "completed"
        assert data["failed"] >= 1

    @pytest.mark.asyncio
    async def test_gitignore_skip_counts_as_ignored(self, tmp_path):
        """Covers is_ignored=True branch (lines 283-288)."""
        from backend.tools.rag import ingest_docs

        md_file = tmp_path / "ignored.md"
        md_file.write_text("# Ignored")

        with patch("backend.tools.rag.settings") as mock_settings:
            mock_settings.enable_file_operations = True
            with patch("backend.tools.fs._confine", return_value=(tmp_path, "")):
                with patch(
                    "backend.services.ignore.load_ignore_spec", return_value=None
                ):
                    # All files are ignored → no_files result
                    with patch("backend.services.ignore.is_ignored", return_value=True):
                        result = await ingest_docs(
                            ctx=ToolContext(),
                            directory_path=str(tmp_path),
                            collection="articles",
                            glob_pattern="**/*.md",
                        )
        data = json.loads(result)
        assert data["status"] == "no_files"
        assert data["skipped_ignored"] >= 1

    @pytest.mark.asyncio
    async def test_always_skip_dirs_counted_in_ignored(self, tmp_path):
        """Dirs em ALWAYS_SKIP_DIRS contam em skipped_ignored (não silenciados).

        Com walk_files, __pycache__ é PODADO durante o os.walk (a subárvore
        nem é varrida) e conta como 1 em skipped_ignored — antes do fix de
        performance, rglob varria a árvore inteira e contava arquivo a arquivo.
        """
        from backend.services.ignore import ALWAYS_SKIP_DIRS
        from backend.tools.rag import ingest_docs

        # Cria um arquivo .py dentro de __pycache__ (dir em ALWAYS_SKIP_DIRS)
        cache_dir = tmp_path / "__pycache__"
        cache_dir.mkdir()
        (cache_dir / "module.py").write_text("# cached module")

        # Cria também um arquivo .py legítimo fora de dirs ignorados
        (tmp_path / "app.py").write_text("# main app")

        with patch("backend.tools.rag.settings") as mock_settings:
            mock_settings.enable_file_operations = True
            with patch("backend.tools.fs._confine", return_value=(tmp_path, "")):
                with patch(
                    "backend.services.ignore.load_ignore_spec", return_value=None
                ):
                    # Não mockamos is_ignored — usa a implementação real
                    with patch(
                        "backend.tools.rag.embedding",
                        AsyncMock(
                            return_value=json.dumps(
                                {"status": "fire_and_forget", "queue_id": "q1"}
                            )
                        ),
                    ):
                        result = await ingest_docs(
                            ctx=ToolContext(),
                            directory_path=str(tmp_path),
                            collection="code",
                            glob_pattern="**/*.py",
                        )
        data = json.loads(result)
        # app.py deve ser indexado; __pycache__/module.py deve ser contado como ignorado
        assert data["status"] == "completed"
        assert data["total_files"] == 1, "apenas app.py deve ser indexado"
        assert data["skipped_ignored"] == 1, (
            "__pycache__/module.py deve contar como ignorado"
        )

    @pytest.mark.asyncio
    async def test_file_read_error_counts_as_failure(self, tmp_path):
        """Covers file read exception path (lines 310-316)."""
        from unittest.mock import mock_open, patch

        from backend.tools.rag import ingest_docs

        md_file = tmp_path / "unreadable.md"
        md_file.write_text("content")

        with patch("backend.tools.rag.settings") as mock_settings:
            mock_settings.enable_file_operations = True
            with patch("backend.tools.fs._confine", return_value=(tmp_path, "")):
                with patch("backend.services.ignore.is_ignored", return_value=False):
                    with patch(
                        "backend.services.ignore.load_ignore_spec",
                        return_value=None,
                    ):
                        with patch(
                            "pathlib.Path.read_text",
                            side_effect=PermissionError("denied"),
                        ):
                            result = await ingest_docs(
                                ctx=ToolContext(),
                                directory_path=str(tmp_path),
                                collection="articles",
                                glob_pattern="**/*.md",
                            )
        data = json.loads(result)
        assert data["status"] == "completed"
        assert data["failed"] >= 1

    @pytest.mark.asyncio
    async def test_embedding_non_fire_and_forget_counts_as_failure(self, tmp_path):
        """Covers else branch when embedding returns unexpected status (line 340)."""
        from backend.tools.rag import ingest_docs

        md_file = tmp_path / "doc.md"
        md_file.write_text("# Content for embedding")

        # Return something that is NOT fire_and_forget
        mock_result = json.dumps({"status": "error", "reason": "queue full"})

        with patch("backend.tools.rag.settings") as mock_settings:
            mock_settings.enable_file_operations = True
            with patch("backend.tools.fs._confine", return_value=(tmp_path, "")):
                with patch("backend.services.ignore.is_ignored", return_value=False):
                    with patch(
                        "backend.services.ignore.load_ignore_spec",
                        return_value=None,
                    ):
                        with patch(
                            "backend.tools.rag.embedding",
                            AsyncMock(return_value=mock_result),
                        ):
                            result = await ingest_docs(
                                ctx=ToolContext(),
                                directory_path=str(tmp_path),
                                collection="articles",
                                glob_pattern="**/*.md",
                            )
        data = json.loads(result)
        assert data["status"] == "completed"
        assert data["failed"] >= 1

    @pytest.mark.asyncio
    async def test_ingest_chama_embedding_diretamente(self, tmp_path):
        """`ingest_docs` chama `embedding` como função async direta — sem
        passar por adapter compatível com o motor de execução antigo."""
        from backend.tools.rag import ingest_docs

        md_file = tmp_path / "test.md"
        md_file.write_text("# Test\nConteúdo de teste para embedding.")

        mock_result = json.dumps({"status": "fire_and_forget", "queue_id": "q1"})
        mock_emb = AsyncMock(return_value=mock_result)

        with patch("backend.tools.rag.settings") as mock_settings:
            mock_settings.enable_file_operations = True
            mock_settings.embedding_queue_enabled = True
            with patch("backend.tools.fs._confine", return_value=(tmp_path, "")):
                with patch("backend.services.ignore.is_ignored", return_value=False):
                    with patch(
                        "backend.services.ignore.load_ignore_spec",
                        return_value=None,
                    ):
                        with patch("backend.tools.rag.embedding", mock_emb):
                            result = await ingest_docs(
                                ctx=ToolContext(),
                                directory_path=str(tmp_path),
                                collection="articles",
                                glob_pattern="**/*.md",
                            )

        data = json.loads(result)
        assert data["status"] == "completed"
        assert mock_emb.called

    @pytest.mark.asyncio
    async def test_pdf_docx_xlsx_reais_extraem_texto_em_vez_de_binario_cru(
        self, tmp_path
    ):
        """PDF/DOCX/XLSX passavam por `read_text(errors="ignore")` puro —
        um arquivo binário real virava lixo decodificado, indexado no RAG
        sem nenhum valor. Assets reais de `tests/assets/` (não fixtures
        sintéticas) provam que o dispatch por extensão chama os parsers de
        `context_graph/detect.py` de verdade. Erro/borda no mesmo teste:
        um PDF corrompido não pode derrubar a ingestão do diretório inteiro
        — `extract_pdf_text` já devolve string vazia nesse caso (tools
        defensivas, CLAUDE.md #11), então o arquivo só produz zero chunks,
        nunca uma exceção que interrompe os demais."""
        import shutil
        from pathlib import Path

        assets = Path(__file__).resolve().parents[1] / "assets"
        shutil.copy(assets / "sample.pdf", tmp_path / "sample.pdf")
        shutil.copy(assets / "sample.docx", tmp_path / "sample.docx")
        shutil.copy(assets / "sample.xlsx", tmp_path / "sample.xlsx")
        shutil.copy(assets / "corrupted.pdf", tmp_path / "corrupted.pdf")

        from backend.tools.rag import ingest_docs

        capturado: list[str] = []

        async def _fake_embedding(*, text, collection, metadata):
            """Executa a operação  fake embedding."""
            capturado.append(text)
            return json.dumps({"status": "fire_and_forget", "queue_id": "q1"})

        with patch("backend.tools.rag.settings") as mock_settings:
            mock_settings.enable_file_operations = True
            with patch("backend.tools.fs._confine", return_value=(tmp_path, "")):
                with patch("backend.services.ignore.is_ignored", return_value=False):
                    with patch(
                        "backend.services.ignore.load_ignore_spec",
                        return_value=None,
                    ):
                        with patch("backend.tools.rag.embedding", _fake_embedding):
                            result = await ingest_docs(
                                ctx=ToolContext(),
                                directory_path=str(tmp_path),
                                collection="articles",
                                glob_pattern="**/*",
                            )

        data = json.loads(result)
        assert data["status"] == "completed"

        todo_texto = "\n".join(capturado)
        assert "Vectora RAG teste de extracao de PDF" in todo_texto
        assert "Vectora RAG teste" in todo_texto  # heading do DOCX
        assert "coluna_a" in todo_texto  # tabela do DOCX e planilha do XLSX
        # PDF corrompido: zero chunks pra ele, mas não quebra os demais.
        assert "not a real pdf" not in todo_texto

    async def test_path_traversal_e_rejeitado_de_verdade(self, tmp_path, monkeypatch):
        """`directory_path` vem do modelo (tool call) — precisa da mesma
        defesa forte que `file_read`/`file_write` já usam
        (`resolve_within_workspace`), não a checagem leve de
        `is_safe_file_path`. Sem mockar `_confine`: workspace real
        confinado a `tmp_path`, tenta escapar via `..` de verdade."""
        from backend.vtypes import Workspace
        from backend.workspace import workspace as ws_mod

        ws = Workspace(
            id="testws",
            name="testws",
            cwd=str(tmp_path),
            created_at="2024-01-01T00:00:00+00:00",
            trusted=True,
            hooks_approved=True,
        )
        monkeypatch.setattr(
            ws_mod.workspace_registry,
            "get",
            lambda wid: ws if wid == "testws" else None,
        )

        from backend.tools.rag import ingest_docs

        with patch("backend.tools.rag.settings") as mock_settings:
            mock_settings.enable_file_operations = True
            result = await ingest_docs(
                ctx=ToolContext(workspace_id="testws"),
                directory_path="../../etc",
                collection="articles",
            )

        assert "fora do workspace" in result.lower() or "error" in result.lower()

    @pytest.mark.asyncio
    async def test_path_dentro_do_workspace_continua_funcionando(
        self, tmp_path, monkeypatch
    ):
        """Regressão: path legítimo (relativo, dentro do workspace) segue
        indexando normalmente com a defesa forte — não é só o caminho de
        rejeição que muda."""
        from backend.vtypes import Workspace
        from backend.workspace import workspace as ws_mod

        (tmp_path / "docs").mkdir()
        (tmp_path / "docs" / "a.md").write_text("# A")

        ws = Workspace(
            id="testws",
            name="testws",
            cwd=str(tmp_path),
            created_at="2024-01-01T00:00:00+00:00",
            trusted=True,
            hooks_approved=True,
        )
        monkeypatch.setattr(
            ws_mod.workspace_registry,
            "get",
            lambda wid: ws if wid == "testws" else None,
        )

        from backend.tools.rag import ingest_docs

        with patch("backend.tools.rag.settings") as mock_settings:
            mock_settings.enable_file_operations = True
            with patch(
                "backend.tools.rag.embedding",
                AsyncMock(
                    return_value=json.dumps(
                        {"status": "fire_and_forget", "queue_id": "q1"}
                    )
                ),
            ):
                result = await ingest_docs(
                    ctx=ToolContext(workspace_id="testws"),
                    directory_path="docs",
                    collection="articles",
                    glob_pattern="**/*.md",
                )

        data = json.loads(result)
        assert data["status"] == "completed"
        assert data["total_files"] == 1


class TestManageRetriever:
    """Tool de gestão do RAG (list / delete / purge)."""

    @pytest.mark.asyncio
    async def test_delete_without_source_errors_early(self):
        """Verifica o comportamento de delete without source errors early."""
        from backend.tools.rag import manage_retriever

        result = await manage_retriever(action="delete", collection="web_cache")
        data = json.loads(result)
        assert data["status"] == "error"
        assert "source" in data["error"]

    @pytest.mark.asyncio
    async def test_purge_drops_table(self):
        """Verifica o comportamento de purge drops table."""
        from backend.tools.rag import manage_retriever

        mock_backend = AsyncMock()
        with patch(
            "backend.storage.factory.get_vector_store_backend",
            AsyncMock(return_value=mock_backend),
        ):
            result = await manage_retriever(action="purge", collection="web_cache")
        data = json.loads(result)
        assert data["status"] == "purged"
        mock_backend.purge.assert_awaited_once_with("web_cache")

    @pytest.mark.asyncio
    async def test_list_missing_collection(self):
        """Verifica o comportamento de list missing collection."""
        from backend.tools.rag import manage_retriever

        mock_backend = AsyncMock()
        mock_backend.list_rows = AsyncMock(return_value=[])
        with patch(
            "backend.storage.factory.get_vector_store_backend",
            AsyncMock(return_value=mock_backend),
        ):
            result = await manage_retriever(action="list", collection="ghost")
        data = json.loads(result)
        assert data["status"] == "no_results"

    @staticmethod
    def _mock_backend_with_rows(rows_meta: list[dict]) -> AsyncMock:
        """Monta um `VectorStoreBackend` fake cujo `list_rows()` devolve
        `VectorRow`s com os metadados dados (ids sintéticos a1, b2, c3...)."""
        from backend.storage.vectorstore.base import VectorRow

        ids = [f"{chr(97 + i)}{i + 1}" for i in range(len(rows_meta))]
        rows = [
            VectorRow(id=doc_id, vector=[], text="", metadata=meta)
            for doc_id, meta in zip(ids, rows_meta, strict=True)
        ]
        mock_backend = AsyncMock()
        mock_backend.list_rows = AsyncMock(return_value=rows)
        mock_backend.delete = AsyncMock(
            side_effect=lambda _collection, matched_ids: len(matched_ids)
        )
        return mock_backend

    @pytest.mark.asyncio
    async def test_list_returns_documents_via_pandas(self):
        """Erro/borda coberto no mesmo teste: metadata sem 'source' cai no
        fallback vazio em vez de KeyError."""
        from backend.tools.rag import manage_retriever

        mock_backend = self._mock_backend_with_rows(
            [
                {
                    "source": "https://github.com/brunosrz/AbilitySystem",
                    "title": "Ability System",
                    "origin": "web_search",
                },
                {"source": "docs/manual.md", "title": "Manual"},
            ]
        )
        with patch(
            "backend.storage.factory.get_vector_store_backend",
            AsyncMock(return_value=mock_backend),
        ):
            result = await manage_retriever(action="list", collection="web_cache")
        data = json.loads(result)
        assert data["status"] == "success"
        assert data["count"] == 2
        assert {d["source"] for d in data["documents"]} == {
            "https://github.com/brunosrz/AbilitySystem",
            "docs/manual.md",
        }

    @pytest.mark.asyncio
    async def test_delete_matches_by_source_via_pandas(self):
        """Delete filtra por substring case-insensitive no source/url/title."""
        from backend.tools.rag import manage_retriever

        mock_backend = self._mock_backend_with_rows(
            [
                {"source": "godot-gameplay-systems/foo"},
                {"source": "github.com/brunosrz/AbilitySystem"},
                {"title": "godot-gameplay-systems wiki"},
            ]
        )
        with patch(
            "backend.storage.factory.get_vector_store_backend",
            AsyncMock(return_value=mock_backend),
        ):
            result = await manage_retriever(
                action="delete",
                collection="web_cache",
                source="godot-gameplay-systems",
            )
        data = json.loads(result)
        assert data["status"] == "deleted"
        assert data["deleted"] == 2
        assert set(data["ids"]) == {"a1", "c3"}
        mock_backend.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delete_no_match_via_pandas(self):
        """Delete sem match retorna no_match, sem chamar backend.delete."""
        from backend.tools.rag import manage_retriever

        mock_backend = self._mock_backend_with_rows([{"source": "docs/manual.md"}])
        with patch(
            "backend.storage.factory.get_vector_store_backend",
            AsyncMock(return_value=mock_backend),
        ):
            result = await manage_retriever(
                action="delete", collection="web_cache", source="inexistente"
            )
        data = json.loads(result)
        assert data["status"] == "no_match"
        mock_backend.delete.assert_not_awaited()


# ---------------------------------------------------------------------------
# _build_reranker — Cohere ↔ VoyageAI
# ---------------------------------------------------------------------------


class TestBuildReranker:
    def test_cohere_without_key_returns_none(self):
        """Verifica o comportamento de cohere without key returns none."""
        from backend.tools.rag import _build_reranker

        with patch("backend.tools.rag.settings") as ms:
            ms.reranker_type = "cohere"
            ms.get_cohere_api_key.return_value = None
            assert _build_reranker() is None

    def test_voyage_without_key_returns_none(self):
        """Verifica o comportamento de voyage without key returns none."""
        from backend.tools.rag import _build_reranker

        with patch("backend.tools.rag.settings") as ms:
            ms.reranker_type = "voyage"
            ms.voyage_api_key = None
            assert _build_reranker() is None

    def test_unknown_type_returns_none(self):
        """Verifica o comportamento de unknown type returns none."""
        from backend.tools.rag import _build_reranker

        with patch("backend.tools.rag.settings") as ms:
            ms.reranker_type = "qualquer-outro"
            assert _build_reranker() is None

    def test_cohere_client_construction_failure_returns_none(self):
        """Erro/borda: qualquer falha ao montar o client nativo (ex.: key
        vazia detectada dentro do construtor) é capturada e vira None, nunca
        propaga pro chamador de `_build_reranker`."""
        from backend.tools.rag import _build_reranker

        with patch("backend.tools.rag.settings") as ms:
            ms.reranker_type = "cohere"
            # get_cohere_api_key() devolve algo não-vazio (passa no guard de
            # _build_cohere_reranker), mas CohereClient valida de novo e
            # rejeita string vazia — simula um estado inconsistente real.
            ms.get_cohere_api_key.return_value = " "
            assert _build_reranker() is None

    def test_voyage_with_key_returns_voyage_reranker(self):
        """Verifica o comportamento de voyage with key returns voyage reranker."""
        from backend.llm.voyage.rerank import VectoraVoyageRerank
        from backend.tools.rag import _build_reranker

        with patch("backend.tools.rag.settings") as ms:
            ms.reranker_type = "voyage"
            ms.voyage_api_key = "voy-1"
            ms.voyage_rerank_model = "rerank-2"
            ms.reranker_top_k = 5
            ms.get_cohere_api_key.return_value = None  # sem secundário → voyage puro
            out = _build_reranker()
        assert isinstance(out, VectoraVoyageRerank)
        assert out.model == "rerank-2"
        assert out.top_k == 5

    def test_reranker_disabled_returns_none(self):
        # reranker_enabled=False nos settings de runtime → sem rerank (None).
        """Verifica o comportamento de reranker disabled returns none."""
        from backend.tools import rag as rag_mod
        from backend.tools.rag import _build_reranker

        with (
            patch("backend.tools.rag.settings") as ms,
            patch.object(
                rag_mod,
                "_rag_runtime",
                lambda: {"reranker_enabled": False, "reranker_top_k": 5},
            ),
        ):
            ms.reranker_type = "cohere"
            ms.get_cohere_api_key.return_value = "ck-1"
            assert _build_reranker() is None

    def test_reranker_provider_pref_forces_voyage(self):
        """Verifica o comportamento de reranker provider pref forces voyage."""
        from backend.llm.voyage.rerank import VectoraVoyageRerank
        from backend.tools import rag as rag_mod
        from backend.tools.rag import _build_reranker

        with (
            patch("backend.tools.rag.settings") as ms,
            patch.object(
                rag_mod,
                "_rag_runtime",
                lambda: {
                    "reranker_enabled": True,
                    "reranker_top_k": 5,
                    "rerank_provider": "voyage",
                },
            ),
        ):
            ms.reranker_type = "cohere"  # mas a preferência força voyage
            ms.voyage_api_key = "voy-1"
            ms.voyage_rerank_model = "rerank-2"
            ms.get_cohere_api_key.return_value = None
            out = _build_reranker()
        assert isinstance(out, VectoraVoyageRerank)

    def test_both_configured_returns_fallback_reranker(self):
        """Verifica o comportamento de both configured returns fallback reranker."""
        from backend.llm.fallback_reranker import FallbackReranker
        from backend.tools.rag import _build_reranker

        with patch("backend.tools.rag.settings") as ms:
            ms.reranker_type = "cohere"
            ms.get_cohere_api_key.return_value = "ck-1"
            ms.reranker_model = "rerank-english-v3.0"
            ms.voyage_api_key = "voy-1"
            ms.voyage_rerank_model = "rerank-2"
            ms.reranker_top_k = 5
            out = _build_reranker()
        assert isinstance(out, FallbackReranker)
        assert out.primary_id == "cohere:rerank-english-v3.0"
        assert out.secondary_id == "voyage:rerank-2"


class TestFallbackRerankerBehavior:
    def _wrap(self, primary, secondary):
        """Executa a operação  wrap."""
        from backend.llm.fallback_reranker import FallbackReranker

        return FallbackReranker(
            primary,
            secondary,
            primary_id="cohere:r",
            secondary_id="voyage:r",
        )

    def test_compress_primary_ok_no_switch(self):
        """Verifica o comportamento de compress primary ok no switch."""
        from backend.llm import provider_fallback as pf

        pf.drain_switches()
        primary = MagicMock()
        primary.compress_documents.return_value = ["doc-primary"]
        secondary = MagicMock()
        out = self._wrap(primary, secondary).compress_documents(["d"], "q")
        assert out == ["doc-primary"]
        secondary.compress_documents.assert_not_called()
        assert pf.drain_switches() == []

    def test_compress_primary_quota_switches(self):
        """Verifica o comportamento de compress primary quota switches."""
        from backend.llm import provider_fallback as pf

        pf.drain_switches()
        primary = MagicMock()
        primary.compress_documents.side_effect = Exception("429 quota")
        secondary = MagicMock()
        secondary.compress_documents.return_value = ["doc-secondary"]
        out = self._wrap(primary, secondary).compress_documents(["d"], "q")
        assert out == ["doc-secondary"]
        assert pf.drain_switches() == [{"from": "cohere:r", "to": "voyage:r"}]

    def test_compress_non_quota_reraises(self):
        """Verifica o comportamento de compress non quota reraises."""
        primary = MagicMock()
        primary.compress_documents.side_effect = ValueError("boom")
        secondary = MagicMock()
        with pytest.raises(ValueError):
            self._wrap(primary, secondary).compress_documents(["d"], "q")
        secondary.compress_documents.assert_not_called()

    @pytest.mark.asyncio
    async def test_acompress_quota_switches(self):
        """Verifica o comportamento de acompress quota switches."""
        from backend.llm import provider_fallback as pf

        pf.drain_switches()
        primary = MagicMock()
        primary.acompress_documents = AsyncMock(side_effect=Exception("rate limit"))
        secondary = MagicMock()
        secondary.acompress_documents = AsyncMock(return_value=["async-secondary"])
        out = await self._wrap(primary, secondary).acompress_documents(["d"], "q")
        assert out == ["async-secondary"]
        assert pf.drain_switches() == [{"from": "cohere:r", "to": "voyage:r"}]
