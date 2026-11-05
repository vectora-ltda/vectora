"""Handler /rag — settings de RAG + listagem/limpeza de coleções."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request

from backend.storage.vectorstore.base import VectorRow


@dataclass
class _FakeVectorStoreBackend:
    """Dublê de `VectorStoreBackend` — mesma interface que LanceDBBackend/
    QdrantBackend implementam, sem tocar storage real. `rows_by_collection`
    simula o que `list_rows()` devolveria por coleção; `broken` simula uma
    coleção que levanta exceção ao ler (schema corrompido, timeout, etc)."""

    rows_by_collection: dict[str, list[VectorRow]] = field(default_factory=dict)
    broken: set[str] = field(default_factory=set)

    async def list_collections(self) -> list[str]:
        return list(self.rows_by_collection)

    async def list_rows(self, collection: str) -> list[VectorRow]:
        if collection in self.broken:
            raise RuntimeError("tabela corrompida")
        return self.rows_by_collection.get(collection, [])

    async def count(self, collection: str) -> int | None:
        if collection in self.broken:
            return None
        return len(self.rows_by_collection.get(collection, []))

    async def purge(self, collection: str) -> None:
        if collection not in self.rows_by_collection:
            raise RuntimeError(f"coleção {collection!r} não encontrada")
        del self.rows_by_collection[collection]

    async def search(self, collection, query_vector, limit):  # pragma: no cover
        raise NotImplementedError

    async def upsert(self, collection, rows):  # pragma: no cover
        raise NotImplementedError

    async def delete(self, collection, ids):  # pragma: no cover
        raise NotImplementedError


def _row(doc_id: str, metadata: dict) -> VectorRow:
    return VectorRow(id=doc_id, vector=[], text="", metadata=metadata)


def _fake_request() -> Request:
    """Request mínimo pros handlers que exigem checagem de dono — sem
    usuário autenticado, ``require_workspace_access`` trata como CLI local
    (sempre privilegiado)."""
    req = MagicMock(spec=Request)
    req.state = MagicMock()
    req.state.user = None
    return req


@pytest.fixture(autouse=True)
def _bypass_workspace_ownership():
    """As classes deste arquivo testam a lógica de agregação de coleções,
    não o gate de dono do workspace (coberto em test_workspaces_view.py) —
    mantém o mock local só pra não travar em request/workspace reais."""
    with patch(
        "backend.api.handlers.workspaces.require_workspace_access",
        return_value=None,
    ):
        yield


class TestRagSettingsEndpoints:
    @pytest.mark.asyncio
    async def test_get_returns_runtime_settings(self, tmp_path):
        from backend.api.handlers import rag as handler
        from backend.workspace.runtime_settings import RuntimeSettings

        rs = RuntimeSettings(path=tmp_path / "settings.json")
        # O handler importa runtime_settings localmente; patcha o módulo origem.
        with patch("backend.workspace.runtime_settings.runtime_settings", rs):
            out = await handler.get_rag_settings()
        assert out["reranker_enabled"] is True
        assert out["reranker_top_k"] == 5

    @pytest.mark.asyncio
    async def test_get_exposes_rerank_provider_availability(self, tmp_path):
        """Regressão ao vivo: escolher cohere/voyage sem a key configurada
        fazia o reranking parar de rodar em silêncio (_build_reranker
        devolve None). O painel precisa saber ANTES de deixar escolher."""
        from backend.api.handlers import rag as handler
        from backend.settings import settings
        from backend.workspace.runtime_settings import RuntimeSettings

        rs = RuntimeSettings(path=tmp_path / "settings.json")
        with (
            patch("backend.workspace.runtime_settings.runtime_settings", rs),
            patch.object(type(settings), "get_cohere_api_key", lambda self: "co-key"),
            patch.object(settings, "voyage_api_key", None),
            patch.object(settings, "openrouter_api_key", None),
        ):
            out = await handler.get_rag_settings()

        assert out["rerank_provider_available"] == {
            "cohere": True,
            "voyage": False,
            "openrouter": False,
        }

    @pytest.mark.asyncio
    async def test_get_reports_both_unavailable_when_no_keys_configured(self, tmp_path):
        """Edge — nem Cohere nem Voyage configurados: os dois False, sem
        levantar erro (o painel deve mostrar os 2 desabilitados)."""
        from backend.api.handlers import rag as handler
        from backend.settings import settings
        from backend.workspace.runtime_settings import RuntimeSettings

        rs = RuntimeSettings(path=tmp_path / "settings.json")
        with (
            patch("backend.workspace.runtime_settings.runtime_settings", rs),
            patch.object(type(settings), "get_cohere_api_key", lambda self: None),
            patch.object(settings, "voyage_api_key", None),
            patch.object(settings, "openrouter_api_key", None),
        ):
            out = await handler.get_rag_settings()

        assert out["rerank_provider_available"] == {
            "cohere": False,
            "voyage": False,
            "openrouter": False,
        }

    @pytest.mark.asyncio
    async def test_get_openrouter_available_only_with_key_and_model(self, tmp_path):
        """openrouter só fica disponível com key E modelo de rerank
        configurados — os dois, não só um (mesma checagem que
        `_build_openrouter_reranker` já faz antes de instanciar o client)."""
        from backend.api.handlers import rag as handler
        from backend.settings import settings
        from backend.workspace.runtime_settings import RuntimeSettings

        rs = RuntimeSettings(path=tmp_path / "settings.json")
        with (
            patch("backend.workspace.runtime_settings.runtime_settings", rs),
            patch.object(type(settings), "get_cohere_api_key", lambda self: None),
            patch.object(settings, "voyage_api_key", None),
            patch.object(settings, "openrouter_api_key", "or-key"),
            patch(
                "backend.settings.configured_gateway_model",
                lambda provider, capability: (
                    "some/model"
                    if (provider, capability) == ("openrouter", "rerank")
                    else ""
                ),
            ),
        ):
            out = await handler.get_rag_settings()

        assert out["rerank_provider_available"]["openrouter"] is True

    @pytest.mark.asyncio
    async def test_get_openrouter_key_sem_modelo_configurado_fica_indisponivel(
        self, tmp_path
    ):
        """Bad path: key presente mas sem modelo de rerank escolhido —
        ainda indisponível, não é suficiente ter só a key."""
        from backend.api.handlers import rag as handler
        from backend.settings import settings
        from backend.workspace.runtime_settings import RuntimeSettings

        rs = RuntimeSettings(path=tmp_path / "settings.json")
        with (
            patch("backend.workspace.runtime_settings.runtime_settings", rs),
            patch.object(settings, "openrouter_api_key", "or-key"),
            patch(
                "backend.settings.configured_gateway_model",
                lambda provider, capability: "",
            ),
        ):
            out = await handler.get_rag_settings()

        assert out["rerank_provider_available"]["openrouter"] is False

    @pytest.mark.asyncio
    async def test_patch_updates_and_returns(self, tmp_path):
        from backend.api.handlers import rag as handler
        from backend.api.handlers.rag import RagSettingsBody
        from backend.workspace.runtime_settings import RuntimeSettings

        rs = RuntimeSettings(path=tmp_path / "settings.json")
        with patch("backend.workspace.runtime_settings.runtime_settings", rs):
            out = await handler.patch_rag_settings(
                RagSettingsBody(reranker_enabled=False, reranker_top_k=9)
            )
        assert out["reranker_enabled"] is False
        assert out["reranker_top_k"] == 9


class TestRagCollections:
    @pytest.mark.asyncio
    async def test_list_empty_when_backend_has_no_collections(self):
        from backend.api.handlers import rag as handler

        fake = _FakeVectorStoreBackend()
        with patch(
            "backend.storage.factory.get_vector_store_backend",
            AsyncMock(return_value=fake),
        ):
            out = await handler.list_collections()
        assert out == {"collections": []}

    @pytest.mark.asyncio
    async def test_delete_raises_404_when_collection_missing(self):
        from fastapi import HTTPException

        from backend.api.handlers import rag as handler

        fake = _FakeVectorStoreBackend()
        with patch(
            "backend.storage.factory.get_vector_store_backend",
            AsyncMock(return_value=fake),
        ):
            with pytest.raises(HTTPException) as exc:
                await handler.delete_collection("articles")
        assert exc.value.status_code == 404


class TestWorkspaceRagSummary:
    """GET /rag/workspace-summary — RAG é escopo de workspace, não de thread.

    Devolve o que já está indexado NAQUELE workspace, contando documentos
    por coleção cujo metadata.workspace_id bate — independente de thread ou
    de qualquer evento de streaming em andamento.
    """

    @pytest.mark.asyncio
    async def test_empty_when_no_collections(self):
        from backend.api.handlers import rag as handler

        fake = _FakeVectorStoreBackend()
        with patch(
            "backend.storage.factory.get_vector_store_backend",
            AsyncMock(return_value=fake),
        ):
            out = await handler.get_workspace_rag_summary(_fake_request(), "ws-1")
        assert out == {"collections": []}

    @pytest.mark.asyncio
    async def test_counts_only_rows_matching_workspace_id(self):
        from backend.api.handlers import rag as handler

        fake = _FakeVectorStoreBackend(
            rows_by_collection={
                "articles": [
                    _row("1", {"workspace_id": "ws-1", "source": "a.md"}),
                    _row("2", {"workspace_id": "ws-2", "source": "b.md"}),
                    _row("3", {"workspace_id": "ws-1", "source": "c.md"}),
                ]
            }
        )
        with patch(
            "backend.storage.factory.get_vector_store_backend",
            AsyncMock(return_value=fake),
        ):
            out = await handler.get_workspace_rag_summary(_fake_request(), "ws-1")

        assert out == {"collections": [{"name": "articles", "count": 2}]}

    @pytest.mark.asyncio
    async def test_omits_collections_with_zero_matches(self):
        from backend.api.handlers import rag as handler

        fake = _FakeVectorStoreBackend(
            rows_by_collection={"web_cache": [_row("1", {"workspace_id": "ws-other"})]}
        )
        with patch(
            "backend.storage.factory.get_vector_store_backend",
            AsyncMock(return_value=fake),
        ):
            out = await handler.get_workspace_rag_summary(_fake_request(), "ws-1")

        assert out == {"collections": []}

    @pytest.mark.asyncio
    async def test_tolerates_one_broken_collection_and_still_reports_others(self):
        """Edge — uma coleção corrompida/sem schema não deve derrubar as demais."""
        from backend.api.handlers import rag as handler

        fake = _FakeVectorStoreBackend(
            rows_by_collection={
                "broken": [],
                "articles": [_row("1", {"workspace_id": "ws-1"})],
            },
            broken={"broken"},
        )
        with patch(
            "backend.storage.factory.get_vector_store_backend",
            AsyncMock(return_value=fake),
        ):
            out = await handler.get_workspace_rag_summary(_fake_request(), "ws-1")

        assert out == {"collections": [{"name": "articles", "count": 1}]}


class TestRagSearch:
    """POST /rag/search — busca direta do usuário, mesma vector_search do agente."""

    @pytest.mark.asyncio
    async def test_search_with_explicit_collection_returns_sorted_results(self):
        import json

        from backend.api.handlers import rag as handler
        from backend.api.handlers.rag import RagSearchBody

        raw = json.dumps(
            {
                "status": "success",
                "results": [
                    {"content": "b", "score": 0.9},
                    {"content": "a", "score": 0.1},
                ],
            }
        )
        with patch("backend.tools.rag.vector_search", AsyncMock(return_value=raw)):
            out = await handler.search_rag(
                _fake_request(),
                RagSearchBody(query="oi", collection="articles", limit=5),
            )

        assert out["query"] == "oi"
        assert [r["content"] for r in out["results"]] == ["a", "b"]
        assert all(r["collection"] == "articles" for r in out["results"])

    @pytest.mark.asyncio
    async def test_search_without_collection_or_workspace_falls_back_to_articles(self):
        from backend.api.handlers import rag as handler
        from backend.api.handlers.rag import RagSearchBody

        mocked = AsyncMock(return_value='{"status": "no_results", "results": []}')
        with patch("backend.tools.rag.vector_search", mocked):
            out = await handler.search_rag(_fake_request(), RagSearchBody(query="oi"))

        mocked.assert_awaited_once()
        _, kwargs = mocked.call_args
        assert kwargs["query"] == "oi"
        assert kwargs["collection"] == "articles"
        assert kwargs["limit"] == 5
        assert out["results"] == []

    @pytest.mark.asyncio
    async def test_search_with_workspace_but_nothing_indexed_returns_empty(self):
        """Edge — workspace sem nenhuma coleção indexada não deve chamar vector_search."""
        from backend.api.handlers import rag as handler
        from backend.api.handlers.rag import RagSearchBody

        with (
            patch.object(
                handler,
                "get_workspace_rag_summary",
                AsyncMock(return_value={"collections": []}),
            ),
            patch("backend.tools.rag.vector_search", AsyncMock()) as mocked,
        ):
            out = await handler.search_rag(
                _fake_request(), RagSearchBody(query="oi", workspace_id="ws-vazio")
            )

        mocked.assert_not_awaited()
        assert out == {"results": []}


class TestRagCollectionsRealLanceDB:
    """LanceDB é embedded (file-based, sem rede) — roda contra dados reais,
    validando o FORMATO do retorno (chaves/tipos), não conteúdo fixo.

    Mantém as classes TestRagCollections/TestWorkspaceRagSummary acima (que
    mockam `get_vector_store_backend`) — esta é a 2ª passada, sem mock nenhum
    de storage, só a instância Cohere/embedding continua fora do escopo (RAG
    handler nunca chama embedding pra listar/gerenciar coleções).

    `lancedb.connect_async()` trava de forma determinística em CI (Linux) —
    ver comentário completo em `pyproject.toml` (seção `timeout`). Isolado
    em step separado do workflow (subprocesso real do SO); o marcador aqui
    só serve pra excluir esses testes da suíte principal via
    '-m "not real_lancedb"'.
    """

    pytestmark = pytest.mark.real_lancedb

    @pytest.fixture(autouse=True)
    def _reset_vector_store_singleton(self, monkeypatch):
        """`get_vector_store_backend()` cacheia um singleton por processo —
        sem resetar, o 2º teste desta classe reusaria o `LanceDBBackend` do
        1º, apontando pro `tmp_path` errado.

        Também força `storage_mode="lite"` explicitamente: a classe testa
        LanceDB de propósito, mas `get_effective_storage_mode()` lê estado
        global (`settings.storage_mode` + cache de licença) que outro teste
        da suíte pode ter deixado em "complete" — sem isso, o teste vira
        Qdrant por engano dependendo da ordem de execução (mesma lição de
        hermeticidade: nunca depender de estado ambiente deixado por outro
        teste)."""
        from backend.storage import factory

        monkeypatch.setattr(
            "backend.services.license.get_effective_storage_mode", lambda: "lite"
        )
        factory._reset_singletons()
        yield
        factory._reset_singletons()

    @pytest.mark.asyncio
    async def test_list_collections_shape_with_real_table(self, tmp_path):
        import lancedb
        import pyarrow as pa

        from backend.api.handlers import rag as handler
        from backend.settings import settings

        db = await lancedb.connect_async(str(tmp_path))
        schema = pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("text", pa.string()),
                pa.field("metadata", pa.string()),
            ]
        )
        table = await db.create_table("articles", schema=schema)
        await table.add(
            [{"id": "1", "text": "conteudo", "metadata": '{"workspace_id": "ws-1"}'}]
        )

        with patch.object(settings, "lancedb_dir", tmp_path):
            out = await handler.list_collections()

        assert isinstance(out, dict)
        assert isinstance(out["collections"], list)
        assert len(out["collections"]) == 1
        collection = out["collections"][0]
        assert isinstance(collection["name"], str)
        assert isinstance(collection["count"], int)

    @pytest.mark.asyncio
    async def test_workspace_summary_shape_with_real_table(self, tmp_path):
        import lancedb
        import pyarrow as pa

        from backend.api.handlers import rag as handler
        from backend.settings import settings

        db = await lancedb.connect_async(str(tmp_path))
        schema = pa.schema(
            [
                pa.field("id", pa.string()),
                pa.field("text", pa.string()),
                pa.field("metadata", pa.string()),
            ]
        )
        table = await db.create_table("articles", schema=schema)
        await table.add(
            [
                {"id": "1", "text": "a", "metadata": '{"workspace_id": "ws-real"}'},
                {"id": "2", "text": "b", "metadata": '{"workspace_id": "outro"}'},
            ]
        )

        with patch.object(settings, "lancedb_dir", tmp_path):
            out = await handler.get_workspace_rag_summary(_fake_request(), "ws-real")

        assert isinstance(out, dict)
        assert isinstance(out["collections"], list)
        for entry in out["collections"]:
            assert set(entry.keys()) == {"name", "count"}
            assert isinstance(entry["name"], str)
            assert isinstance(entry["count"], int)
        # workspace_id filtra corretamente: só a linha de "ws-real" conta.
        assert out["collections"][0]["count"] == 1
