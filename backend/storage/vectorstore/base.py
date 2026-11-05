"""Interface de vector store — molde direto da ABC `MemoryProvider` do Hermes
Agent (escrita à mão, sem herdar de framework): um contrato pequeno que
`LanceDBBackend` (lite) e `QdrantBackend` (complete) implementam, pra
`tools/rag.py`/`embedding/background.py` pararem de hardcodar LanceDB.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict


class VectorStoreHealth(BaseModel):
    """Resultado tipado de verificação de integridade do backend vetorial."""

    model_config = ConfigDict(extra="ignore")

    ok: bool | None = None
    error: str | None = None
    tables: list[str] | None = None
    collections: list[str] | None = None
    internal: bool | None = None

    def __getitem__(self, item: str) -> object:
        """Executa a operação getitem."""
        if not hasattr(self, item):
            raise KeyError(item)
        val = getattr(self, item)
        if val is None and item not in self.model_fields_set:
            raise KeyError(item)
        return val

    def __contains__(self, item: object) -> bool:
        """Executa a operação contains."""
        if not isinstance(item, str):
            return False
        return hasattr(self, item) and (
            getattr(self, item) is not None or item in self.model_fields_set
        )

    def get(self, item: str, default: object = None) -> object:
        """Executa a operação get."""
        if item in self:
            return getattr(self, item)
        return default


@dataclass
class VectorRow:
    """Um documento indexado — shape comum entre LanceDB e Qdrant."""

    id: str
    vector: list[float]
    text: str
    metadata: dict[str, Any]


@dataclass
class VectorHit:
    """Resultado de busca — mesmo shape que `tools/rag.py::vector_search`
    já monta hoje a partir do LanceDB, pra reranking/resposta não mudarem."""

    id: str
    score: float
    content: str
    metadata: dict[str, Any]
    collection: str


class VectorStoreBackend(Protocol):
    """Contrato nativo de storage vetorial.

    Erros de rede/timeout são responsabilidade de cada implementação tratar
    (nunca deixar subir cru pro tool caller) — mesma defensividade que
    `_search_one_collection`/`_write_to_lancedb` já praticam hoje.
    """

    async def search(
        self, collection: str, query_vector: list[float], limit: int
    ) -> list[VectorHit]:
        """Top-k por similaridade. Coleção ausente devolve lista vazia."""
        ...

    async def search_text(
        self, collection: str, query: str, limit: int
    ) -> list[VectorHit]:
        """Busca lexical (BM25-like) — usada pela fusão híbrida em
        `tools/rag.py::vector_search` (`settings.rag_hybrid_enabled`) para
        cobrir recall que embeddings tendem a diluir (identificador de
        código, mensagem de erro exata). Coleção ausente ou sem índice de
        texto disponível devolve lista vazia, nunca lança.

        `score` aqui é relevância textual (maior é melhor) — convenção
        OPOSTA à de `search()` (distância, menor é melhor). A fusão em
        `tools/rag.py` usa só a ORDEM de cada lista (Reciprocal Rank
        Fusion), nunca os valores absolutos — a diferença de escala entre
        os dois métodos não precisa ser normalizada."""
        ...

    async def upsert(self, collection: str, rows: list[VectorRow]) -> None:
        """Insere/atualiza documentos. Cria a coleção se não existir."""
        ...

    async def list_rows(self, collection: str) -> list[VectorRow]:
        """Todos os documentos da coleção. Coleção ausente devolve `[]`."""
        ...

    async def delete(self, collection: str, ids: list[str]) -> int:
        """Remove por id. Devolve quantos foram de fato removidos."""
        ...

    async def purge(self, collection: str) -> None:
        """Apaga a coleção inteira. Idempotente — coleção ausente não é erro."""
        ...

    async def list_collections(self) -> list[str]:
        """Nomes de todas as coleções existentes. Backend vazio devolve `[]`."""
        ...

    async def count(self, collection: str) -> int | None:
        """Quantidade de documentos na coleção. `None` se não conseguir
        determinar (coleção corrompida/erro transitório) — nunca lança."""
        ...

    async def health(self) -> VectorStoreHealth:
        """Verifica se o backend está acessível e funcional."""
        ...
