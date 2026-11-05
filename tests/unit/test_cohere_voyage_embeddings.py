"""Embeddings nativos do Cohere (Embed API v2) e da Voyage AI — cliente
HTTP nativo, mesmo padrão de paridade já usado para o OpenRouter
(`test_openrouter_embeddings_rerank.py`).
"""

from __future__ import annotations

import httpx
import pytest

from backend.llm.cohere.client import (
    CohereAuthError,
    CohereClient,
    CohereRateLimitError,
    CohereResponseError,
)
from backend.llm.cohere.embeddings import VectoraCohereEmbeddings
from backend.llm.voyage.client import (
    VoyageAuthError,
    VoyageClient,
    VoyageRateLimitError,
    VoyageResponseError,
)
from backend.llm.voyage.embeddings import VectoraVoyageEmbeddings


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


class TestCohereEmbeddings:
    @pytest.mark.asyncio
    async def test_devolve_um_vetor_por_documento_na_ordem(self):
        captured: dict = {}

        def handler(req):
            captured["body"] = req.content
            return httpx.Response(
                200,
                json={"embeddings": {"float": [[0.1, 0.2], [0.3, 0.4]]}},
            )

        emb = VectoraCohereEmbeddings(
            model="embed-multilingual-v3.0", client=_cohere_client(handler)
        )
        vetores = await emb.aembed_documents(["um", "dois"])

        assert vetores == [[0.1, 0.2], [0.3, 0.4]]
        assert b'"input_type":"search_document"' in captured["body"]

    @pytest.mark.asyncio
    async def test_query_usa_input_type_search_query(self):
        captured: dict = {}

        def handler(req):
            captured["body"] = req.content
            return httpx.Response(200, json={"embeddings": {"float": [[0.1]]}})

        emb = VectoraCohereEmbeddings(
            model="embed-v4.0", client=_cohere_client(handler)
        )
        vetor = await emb.aembed_query("pergunta")

        assert vetor == [0.1]
        assert b'"input_type":"search_query"' in captured["body"]

    @pytest.mark.asyncio
    async def test_batch_maior_que_96_pagina_em_multiplas_chamadas(self):
        chamadas: list[int] = []

        def handler(req):
            import json as _json

            corpo = _json.loads(req.content)
            n = len(corpo["texts"])
            chamadas.append(n)
            return httpx.Response(200, json={"embeddings": {"float": [[0.0]] * n}})

        emb = VectoraCohereEmbeddings(
            model="embed-v4.0", client=_cohere_client(handler)
        )
        textos = [f"texto-{i}" for i in range(150)]
        vetores = await emb.aembed_documents(textos)

        assert len(vetores) == 150
        assert chamadas == [96, 54]

    @pytest.mark.asyncio
    async def test_401_vira_excecao_tipada_distinta_de_429(self):
        def handler_401(_req):
            return httpx.Response(401, json={"message": "invalid api token"})

        emb = VectoraCohereEmbeddings(
            model="embed-v4.0", client=_cohere_client(handler_401)
        )
        with pytest.raises(CohereAuthError):
            await emb.aembed_query("x")

        def handler_429(_req):
            return httpx.Response(429, json={"message": "too many requests"})

        emb2 = VectoraCohereEmbeddings(
            model="embed-v4.0", client=_cohere_client(handler_429)
        )
        with pytest.raises(CohereRateLimitError):
            await emb2.aembed_query("x")

    @pytest.mark.asyncio
    async def test_embeddings_ausente_levanta_erro_tipado_nao_lista_vazia(self):
        def handler(_req):
            return httpx.Response(200, json={"embeddings": {}})

        emb = VectoraCohereEmbeddings(
            model="embed-v4.0", client=_cohere_client(handler)
        )
        with pytest.raises(CohereResponseError):
            await emb.aembed_documents(["a", "b"])


class TestVoyageEmbeddings:
    @pytest.mark.asyncio
    async def test_devolve_um_vetor_por_documento_na_ordem(self):
        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 0, "embedding": [0.1, 0.2]},
                        {"index": 1, "embedding": [0.3, 0.4]},
                    ]
                },
            )

        emb = VectoraVoyageEmbeddings(model="voyage-3", client=_voyage_client(handler))
        vetores = await emb.aembed_documents(["um", "dois"])

        assert vetores == [[0.1, 0.2], [0.3, 0.4]]

    @pytest.mark.asyncio
    async def test_resposta_fora_de_ordem_e_reordenada_pelo_index(self):
        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 1, "embedding": [0.3, 0.4]},
                        {"index": 0, "embedding": [0.1, 0.2]},
                    ]
                },
            )

        emb = VectoraVoyageEmbeddings(model="voyage-3", client=_voyage_client(handler))
        vetores = await emb.aembed_documents(["um", "dois"])

        assert vetores == [[0.1, 0.2], [0.3, 0.4]]

    @pytest.mark.asyncio
    async def test_401_vira_excecao_tipada_distinta_de_429(self):
        def handler_401(_req):
            return httpx.Response(401, json={"detail": "invalid key"})

        emb = VectoraVoyageEmbeddings(
            model="voyage-3", client=_voyage_client(handler_401)
        )
        with pytest.raises(VoyageAuthError):
            await emb.aembed_query("x")

        def handler_429(_req):
            return httpx.Response(429, json={"detail": "rate limited"})

        emb2 = VectoraVoyageEmbeddings(
            model="voyage-3", client=_voyage_client(handler_429)
        )
        with pytest.raises(VoyageRateLimitError):
            await emb2.aembed_query("x")

    @pytest.mark.asyncio
    async def test_data_vazio_levanta_erro_tipado_nao_lista_vazia(self):
        def handler(_req):
            return httpx.Response(200, json={"data": []})

        emb = VectoraVoyageEmbeddings(model="voyage-3", client=_voyage_client(handler))
        with pytest.raises(VoyageResponseError):
            await emb.aembed_documents(["a"])

    @pytest.mark.asyncio
    async def test_indice_faltando_levanta_erro_tipado(self):
        def handler(_req):
            return httpx.Response(
                200, json={"data": [{"index": 0, "embedding": [0.1]}]}
            )

        emb = VectoraVoyageEmbeddings(model="voyage-3", client=_voyage_client(handler))
        with pytest.raises(VoyageResponseError):
            await emb.aembed_documents(["a", "b"])
