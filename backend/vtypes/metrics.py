from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class UIMetrics(BaseModel):
    """Métricas de observabilidade em tempo real (D1.5 — State-Sync Observability)."""

    last_node: str | None = Field(
        default=None, description="Nó que acabou de executar."
    )
    last_node_ms: int | None = Field(
        default=None, description="Latência em ms desse nó."
    )
    total_tokens_session: int | None = Field(
        default=None, description="Tokens acumulados na sessão."
    )
    rag_hits: int | None = Field(
        default=None, description="Buscas RAG que retornaram documentos relevantes."
    )
    rag_misses: int | None = Field(
        default=None, description="Fallbacks para websearch por score < threshold."
    )
    tool_calls: dict[str, int] | None = Field(
        default=None, description="{tool_name: count} de chamadas nesta sessão."
    )
    workspace_id: str | None = Field(
        default=None,
        description="Workspace ativo (espelha session_metadata.workspace_id).",
    )
    manifest_version: int | None = Field(
        default=None,
        description="Versão do manifest carregado (espelha session_metadata).",
    )

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)

    def get(self, item: str, default: Any = None) -> Any:
        return getattr(self, item, default)
