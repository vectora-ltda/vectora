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
    ModelCapabilities,
    clear_capabilities_cache,
    fetch_model_capabilities,
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


class TestApiShow:
    @pytest.mark.asyncio
    async def test_capabilities_do_modelo_sao_lidas(self):
        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "capabilities": ["completion", "tools", "vision", "thinking"],
                    "model_info": {"llama.context_length": 128000},
                },
            )

        caps = await fetch_model_capabilities(_client(handler), "llava:13b")

        assert caps.vision is True
        assert caps.thinking is True
        assert caps.tools is True
        assert caps.embedding is False
        assert caps.context_length == 128000

    @pytest.mark.asyncio
    async def test_modelo_de_embedding_e_reconhecido(self):
        def handler(_req):
            return httpx.Response(200, json={"capabilities": ["embedding"]})

        caps = await fetch_model_capabilities(_client(handler), "nomic-embed-text")

        assert caps.embedding is True
        assert caps.vision is False

    @pytest.mark.asyncio
    async def test_servidor_antigo_sem_capabilities_cai_no_fallback(self):
        """Erro/borda: servidor anterior ao campo `capabilities` — o Hermes
        usa o mesmo fallback (`model_info.*.vision.block_count`)."""

        def handler(_req):
            return httpx.Response(
                200,
                json={
                    "model_info": {
                        "clip.vision.block_count": 32,
                        "llama.context_length": 4096,
                    }
                },
            )

        caps = await fetch_model_capabilities(_client(handler), "llava-antigo")

        assert caps.vision is True
        assert caps.context_length == 4096

    @pytest.mark.asyncio
    async def test_servidor_fora_do_ar_falha_fechado(self):
        """Erro/borda crítico: sem resposta, **nenhuma** capacidade é
        assumida. Falhar aberto faria a UI oferecer o que vai quebrar."""

        def handler(_req):
            raise httpx.ConnectError("connection refused")

        caps = await fetch_model_capabilities(_client(handler), "qualquer")

        assert caps == ModelCapabilities()
        assert not any((caps.vision, caps.thinking, caps.tools, caps.embedding))

    @pytest.mark.asyncio
    async def test_resultado_e_cacheado_por_modelo(self):
        chamadas = 0

        def handler(_req):
            nonlocal chamadas
            chamadas += 1
            return httpx.Response(200, json={"capabilities": ["vision"]})

        client = _client(handler)
        await fetch_model_capabilities(client, "llava:13b")
        await fetch_model_capabilities(client, "llava:13b")

        assert chamadas == 1, "consultou /api/show duas vezes pro mesmo modelo"

    @pytest.mark.asyncio
    async def test_modelos_diferentes_nao_compartilham_cache(self):
        """Erro/borda: cache por servidor em vez de por modelo daria a
        capacidade de um modelo a outro."""
        vistos: list[str] = []

        def handler(req: httpx.Request) -> httpx.Response:
            import json as _json

            vistos.append(_json.loads(req.content)["model"])
            return httpx.Response(200, json={"capabilities": ["vision"]})

        client = _client(handler)
        await fetch_model_capabilities(client, "modelo-a")
        await fetch_model_capabilities(client, "modelo-b")

        assert vistos == ["modelo-a", "modelo-b"]


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
