"""Rerank nativo do Cohere (Rerank API v2) e da Voyage AI — cliente HTTP
nativo, mesmo padrão de `test_openrouter_embeddings_rerank.py`.
"""

from __future__ import annotations

import httpx
import pytest

from backend.llm.cohere.client import CohereClient
from backend.llm.cohere.rerank import VectoraCohereRerank
from backend.llm.voyage.client import VoyageClient
from backend.llm.voyage.rerank import VectoraVoyageRerank
from backend.vtypes.documents import Document


def _cohere_client(handler) -> CohereClient:
    return CohereClient(
        api_key="co-test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _voyage_client(handler) -> VoyageClient:
    return VoyageClient(
        api_key="voyage-test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _docs() -> list[Document]:
    return [
        Document(page_content="doc um", metadata={"id": "1"}),
        Document(page_content="doc dois", metadata={"id": "2"}),
    ]


class TestCohereRerank:
    @pytest.mark.asyncio
    async def test_reordena_por_relevance_score(self):
        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 1, "relevance_score": 0.9},
                        {"index": 0, "relevance_score": 0.2},
                    ]
                },
            )

        rr = VectoraCohereRerank(model="rerank-v3.5", client=_cohere_client(handler))
        saida = await rr.acompress_documents(_docs(), "query")

        assert [d.page_content for d in saida] == ["doc dois", "doc um"]
        assert saida[0].metadata["relevance_score"] == 0.9

    @pytest.mark.asyncio
    async def test_lista_vazia_nao_chama_api(self):
        chamou = False

        def handler(_req):
            nonlocal chamou
            chamou = True
            return httpx.Response(200, json={"results": []})

        rr = VectoraCohereRerank(model="rerank-v3.5", client=_cohere_client(handler))
        saida = await rr.acompress_documents([], "query")

        assert saida == []
        assert chamou is False

    @pytest.mark.asyncio
    async def test_results_vazio_degrada_para_ordem_original(self):
        def handler(_req):
            return httpx.Response(200, json={"results": []})

        rr = VectoraCohereRerank(model="rerank-v3.5", client=_cohere_client(handler))
        docs = _docs()
        saida = await rr.acompress_documents(docs, "query")

        assert saida == docs

    @pytest.mark.asyncio
    async def test_index_fora_do_intervalo_e_ignorado(self):
        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 0, "relevance_score": 0.5},
                        {"index": 99, "relevance_score": 0.9},
                    ]
                },
            )

        rr = VectoraCohereRerank(model="rerank-v3.5", client=_cohere_client(handler))
        saida = await rr.acompress_documents(_docs(), "query")

        assert [d.page_content for d in saida] == ["doc um"]

    @pytest.mark.asyncio
    async def test_top_n_maior_que_total_de_docs_nao_estoura(self):
        capturado: dict = {}

        def handler(req):
            import json as _json

            capturado["body"] = _json.loads(req.content)
            return httpx.Response(200, json={"results": []})

        rr = VectoraCohereRerank(
            model="rerank-v3.5", client=_cohere_client(handler), top_n=50
        )
        await rr.acompress_documents(_docs(), "query")

        assert capturado["body"]["top_n"] == 2


class TestVoyageRerank:
    @pytest.mark.asyncio
    async def test_reordena_por_relevance_score(self):
        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 1, "relevance_score": 0.8},
                        {"index": 0, "relevance_score": 0.1},
                    ]
                },
            )

        rr = VectoraVoyageRerank(model="rerank-2.5", client=_voyage_client(handler))
        saida = await rr.acompress_documents(_docs(), "query")

        assert [d.page_content for d in saida] == ["doc dois", "doc um"]

    @pytest.mark.asyncio
    async def test_lista_vazia_nao_chama_api(self):
        chamou = False

        def handler(_req):
            nonlocal chamou
            chamou = True
            return httpx.Response(200, json={"data": []})

        rr = VectoraVoyageRerank(model="rerank-2.5", client=_voyage_client(handler))
        saida = await rr.acompress_documents([], "query")

        assert saida == []
        assert chamou is False

    @pytest.mark.asyncio
    async def test_data_vazio_degrada_para_ordem_original(self):
        def handler(_req):
            return httpx.Response(200, json={"data": []})

        rr = VectoraVoyageRerank(model="rerank-2.5", client=_voyage_client(handler))
        docs = _docs()
        saida = await rr.acompress_documents(docs, "query")

        assert saida == docs

    @pytest.mark.asyncio
    async def test_erro_de_rede_vira_excecao_tipada(self):
        from backend.llm.voyage.client import VoyageServerError

        def handler(_req):
            return httpx.Response(500, json={"detail": "internal error"})

        rr = VectoraVoyageRerank(model="rerank-2.5", client=_voyage_client(handler))
        with pytest.raises(VoyageServerError):
            await rr.acompress_documents(_docs(), "query")
