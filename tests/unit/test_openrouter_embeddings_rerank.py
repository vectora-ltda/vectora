"""Embeddings e rerank nativos do OpenRouter.

São as duas capacidades que o produto **já promete na UI** e não entregava:
o seletor de embedding oferece OpenRouter (por `OpenAIEmbeddings` com base_url
trocado, que não expõe `input_type` nem `usage.cost`), e o `reranker_type`
tinha sido fechado por engano com a justificativa errada de que o OpenRouter
não teria endpoint de rerank. Tem: ``POST /api/v1/rerank``.
"""

from __future__ import annotations

import httpx
import pytest

from backend.llm.openrouter.client import OpenRouterClient, OpenRouterResponseError
from backend.llm.openrouter.embeddings import OpenRouterEmbeddings
from backend.llm.openrouter.rerank import OpenRouterRerank
from backend.vtypes.documents import Document


def _client(handler) -> OpenRouterClient:
    return OpenRouterClient(
        api_key="sk-or-test",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


class TestEmbeddings:
    @pytest.mark.asyncio
    async def test_devolve_um_vetor_por_documento_na_ordem(self):
        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 0, "embedding": [0.1, 0.2, 0.3]},
                        {"index": 1, "embedding": [0.4, 0.5, 0.6]},
                    ],
                    "usage": {"cost": 0.00001},
                },
            )

        emb = OpenRouterEmbeddings(
            model="openai/text-embedding-3-small", client=_client(handler)
        )
        vetores = await emb.aembed_documents(["um", "dois"])

        assert vetores == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    @pytest.mark.asyncio
    async def test_resposta_fora_de_ordem_e_reordenada_pelo_index(self):
        """Erro/borda: a API não garante a ordem de `data`. Confiar na ordem
        de chegada associa o vetor ao chunk errado — corrupção silenciosa do
        índice, que só aparece como busca ruim muito depois."""

        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"index": 1, "embedding": [9.0]},
                        {"index": 0, "embedding": [1.0]},
                    ]
                },
            )

        emb = OpenRouterEmbeddings(model="m", client=_client(handler))
        assert await emb.aembed_documents(["primeiro", "segundo"]) == [[1.0], [9.0]]

    @pytest.mark.asyncio
    async def test_data_vazio_vira_erro_tipado_e_nao_lista_vazia(self):
        """Erro/borda: devolver `[]` gravaria vetores nulos no índice do RAG.
        Melhor falhar a ingestão do que corromper a busca em silêncio."""

        def handler(_req):
            return httpx.Response(200, json={"data": []})

        emb = OpenRouterEmbeddings(model="m", client=_client(handler))
        with pytest.raises(OpenRouterResponseError):
            await emb.aembed_documents(["algo"])

    @pytest.mark.asyncio
    async def test_item_sem_embedding_tambem_falha(self):
        def handler(_req):
            return httpx.Response(200, json={"data": [{"index": 0}]})

        emb = OpenRouterEmbeddings(model="m", client=_client(handler))
        with pytest.raises(OpenRouterResponseError):
            await emb.aembed_documents(["algo"])

    @pytest.mark.asyncio
    async def test_input_type_diferencia_query_de_documento(self):
        """Parâmetro que o `OpenAIEmbeddings` com base_url trocado não expõe —
        modelos assimétricos precisam saber se é consulta ou documento."""
        capturados: list[dict] = []

        def handler(req: httpx.Request) -> httpx.Response:
            import json as _json

            capturados.append(_json.loads(req.content))
            return httpx.Response(
                200, json={"data": [{"index": 0, "embedding": [1.0]}]}
            )

        emb = OpenRouterEmbeddings(model="m", client=_client(handler))
        await emb.aembed_documents(["doc"])
        await emb.aembed_query("consulta")

        assert capturados[0].get("input_type") == "document"
        assert capturados[1].get("input_type") == "query"


class TestRerank:
    _DOCS = [
        Document(page_content="irrelevante"),
        Document(page_content="muito relevante"),
        Document(page_content="mais ou menos"),
    ]

    def _handler_ok(self, capturados: list | None = None):
        def handler(req: httpx.Request) -> httpx.Response:
            if capturados is not None:
                import json as _json

                capturados.append(_json.loads(req.content))
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 1, "relevance_score": 0.98},
                        {"index": 2, "relevance_score": 0.42},
                        {"index": 0, "relevance_score": 0.01},
                    ]
                },
            )

        return handler

    @pytest.mark.asyncio
    async def test_ordem_segue_o_relevance_score(self):
        rr = OpenRouterRerank(
            model="cohere/rerank-v3.5", client=_client(self._handler_ok()), top_n=3
        )
        saida = await rr.acompress_documents(self._DOCS, "qual é relevante?")

        assert [d.page_content for d in saida] == [
            "muito relevante",
            "mais ou menos",
            "irrelevante",
        ]

    @pytest.mark.asyncio
    async def test_score_vai_no_metadata_pra_ui_poder_mostrar(self):
        rr = OpenRouterRerank(model="m", client=_client(self._handler_ok()), top_n=3)
        saida = await rr.acompress_documents(self._DOCS, "q")

        assert saida[0].metadata["relevance_score"] == pytest.approx(0.98)

    @pytest.mark.asyncio
    async def test_top_n_maior_que_os_documentos_nao_estoura(self):
        """Erro/borda: pedir mais do que existe é comum quando o `top_k` é
        fixo e o bucket tem poucos documentos."""
        rr = OpenRouterRerank(model="m", client=_client(self._handler_ok()), top_n=50)
        saida = await rr.acompress_documents(self._DOCS, "q")

        assert len(saida) == 3

    @pytest.mark.asyncio
    async def test_results_vazio_degrada_pra_ordem_original(self):
        """Erro/borda: zerar os resultados é pior que não reordenar — o
        usuário perderia respostas que a busca já tinha encontrado."""

        def handler(_req):
            return httpx.Response(200, json={"results": []})

        rr = OpenRouterRerank(model="m", client=_client(handler), top_n=3)
        saida = await rr.acompress_documents(self._DOCS, "q")

        assert [d.page_content for d in saida] == [d.page_content for d in self._DOCS]

    @pytest.mark.asyncio
    async def test_index_fora_do_intervalo_e_ignorado(self):
        """Erro/borda: `index` além da lista enviada estouraria IndexError e
        derrubaria a busca inteira."""

        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"index": 99, "relevance_score": 0.9},
                        {"index": 0, "relevance_score": 0.5},
                    ]
                },
            )

        rr = OpenRouterRerank(model="m", client=_client(handler), top_n=3)
        saida = await rr.acompress_documents(self._DOCS, "q")

        assert [d.page_content for d in saida] == ["irrelevante"]

    @pytest.mark.asyncio
    async def test_lista_vazia_de_documentos_nem_chama_a_api(self):
        chamou = False

        def handler(_req):
            nonlocal chamou
            chamou = True
            return httpx.Response(200, json={"results": []})

        rr = OpenRouterRerank(model="m", client=_client(handler), top_n=3)
        assert await rr.acompress_documents([], "q") == []
        assert not chamou, "gastou crédito reordenando lista vazia"


class TestRerankerTypeAceitaOpenRouter:
    def test_literal_inclui_openrouter(self):
        """`f38f7b85` fechou o Literal em cohere/voyage/none com a justificativa
        errada de que o OpenRouter não teria API de rerank."""
        from backend.settings import Settings

        s = Settings(reranker_type="openrouter")  # type: ignore[arg-type]
        assert s.reranker_type == "openrouter"

    def test_valor_fora_do_literal_continua_rejeitado(self):
        """Erro/borda: abrir o Literal não pode virar campo livre — um typo
        desligaria o rerank em silêncio."""
        import pydantic

        from backend.settings import Settings

        with pytest.raises(pydantic.ValidationError):
            Settings(reranker_type="ollama")  # type: ignore[arg-type]
