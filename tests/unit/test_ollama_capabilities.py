"""Capacidades por modelo (`/api/show`) e embeddings (`/api/embed`) do Ollama.

Até aqui a capacidade do Ollama era resolvida por "o usuário configurou
`ollama_image_model`?" — configuração, não detecção. O `/api/show` devolve o
array ``capabilities`` (`vision`, `thinking`, `tools`, `embedding`) e o
`context_length`: é a fonte de verdade, e o Hermes usa exatamente esse
endpoint (`agent/model_metadata.py:1630,1688`).

Falha **fechada** é o invariante crítico: servidor fora do ar não pode
resultar em "tudo suportado", senão a UI oferece o que vai falhar depois.
"""

from __future__ import annotations

import httpx
import pytest

from backend.llm.ollama.capabilities import (
    clear_capabilities_cache,
)
from backend.llm.ollama.client import OllamaClient, OllamaResponseError
from backend.llm.ollama.embeddings import OllamaEmbeddings


def _client(handler) -> OllamaClient:
    return OllamaClient(
        base_url="http://127.0.0.1:11434",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


@pytest.fixture(autouse=True)
def _limpa_cache():
    clear_capabilities_cache()
    yield
    clear_capabilities_cache()


class TestEmbeddings:
    @pytest.mark.asyncio
    async def test_devolve_um_vetor_por_documento(self):
        def handler(_req):
            return httpx.Response(200, json={"embeddings": [[0.1, 0.2], [0.3, 0.4]]})

        emb = OllamaEmbeddings(model="nomic-embed-text", client=_client(handler))
        assert await emb.aembed_documents(["um", "dois"]) == [[0.1, 0.2], [0.3, 0.4]]

    @pytest.mark.asyncio
    async def test_query_devolve_um_vetor_so(self):
        def handler(_req):
            return httpx.Response(200, json={"embeddings": [[0.5]]})

        emb = OllamaEmbeddings(model="m", client=_client(handler))
        assert await emb.aembed_query("consulta") == [0.5]

    @pytest.mark.asyncio
    async def test_embeddings_vazio_vira_erro_e_nao_lista_vazia(self):
        """Erro/borda: `[]` gravaria vetores nulos no índice do RAG — mesma
        trava do OpenRouter. Melhor falhar a ingestão."""

        def handler(_req):
            return httpx.Response(200, json={"embeddings": []})

        emb = OllamaEmbeddings(model="m", client=_client(handler))
        with pytest.raises(OllamaResponseError):
            await emb.aembed_documents(["algo"])

    @pytest.mark.asyncio
    async def test_quantidade_diferente_do_pedido_falha(self):
        """Erro/borda: 2 textos e 1 vetor de volta significa que algum chunk
        ficaria sem embedding — associar na ordem corromperia o índice."""

        def handler(_req):
            return httpx.Response(200, json={"embeddings": [[0.1]]})

        emb = OllamaEmbeddings(model="m", client=_client(handler))
        with pytest.raises(OllamaResponseError, match="2"):
            await emb.aembed_documents(["um", "dois"])

    @pytest.mark.asyncio
    async def test_lista_vazia_nao_chama_a_api(self):
        chamou = False

        def handler(_req):
            nonlocal chamou
            chamou = True
            return httpx.Response(200, json={"embeddings": []})

        emb = OllamaEmbeddings(model="m", client=_client(handler))
        assert await emb.aembed_documents([]) == []
        assert not chamou


class TestFactoryUsaClienteNativo:
    def test_build_ollama_embeddings_devolve_o_nativo(self, monkeypatch):
        from backend.settings import settings as _s
        from backend.storage import factory

        monkeypatch.setattr(_s, "ollama_base_url", "http://127.0.0.1:11434")
        monkeypatch.setattr(_s, "ollama_embedding_model", "nomic-embed-text")

        emb = factory._build_ollama_embeddings()

        assert isinstance(emb, OllamaEmbeddings)
        # Erro/borda: client nativo, sem dependência de SDK externo — um
        # import quebrado falharia alto, não passaria silenciosamente.
        assert emb.client.base_url == "http://127.0.0.1:11434"

    def test_sem_modelo_configurado_devolve_none(self, monkeypatch):
        from backend.settings import settings as _s
        from backend.storage import factory

        monkeypatch.setattr(_s, "ollama_embedding_model", None)

        assert factory._build_ollama_embeddings() is None
